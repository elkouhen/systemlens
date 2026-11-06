"""Regressions of the source extraction and the inventory.

Each test reproduces a finding of the 2026-11-06 review (``BACKLOG.md``,
section 3.2) on a minimal fixture and names the rule it protects.
"""

from pathlib import Path

from systemlens.discovery.build.modules import discover_modules
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.indexing.dto_inventory import materialize_kafka_dto_definitions
from systemlens.indexing.file_inventory import list_repo_files
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing.materializers import (
    materialize_asyncapi_contracts,
    materialize_openapi_contracts,
)
from systemlens.indexing.relations import build_architecture_relations
from systemlens.indexing.service import index_repo
from systemlens.indexing.source_projection import project_java_sources
from systemlens.infrastructure.config import Config
from systemlens.scanner import clear_analysis_caches
from systemlens.scanner.kafka_ast import infer_kafka_endpoints
from systemlens.scanner.rest_mvc import infer_framework_endpoints
from systemlens.storage.sqlite import Store

POM = (
    "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
    "<artifactId>{name}</artifactId><version>1.0.0</version>{deps}</project>"
)


def _module(path: Path, name: str = "svc", openapi_files: tuple[str, ...] = ()) -> DiscoveredModule:
    return DiscoveredModule(
        name=name, path=path, build_system="maven", version=None, kind="library",
        starts_application=True, configuration_example="", openapi_files=openapi_files,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_non_utf8_yaml_file_is_no_contract_and_no_crash(tmp_path: Path) -> None:
    """Review P3: a Latin-1 YAML file aborted the whole index."""
    svc = tmp_path / "svc"
    (svc / "src/main/resources").mkdir(parents=True)
    (svc / "src/main/resources/messages.yml").write_bytes(b"# r\xe9sum\xe9\nkey: value\n")
    (svc / "src/main/resources/openapi.yml").write_bytes(b"openapi: 3.0.0\n# \xe9\n")
    _write(svc / "src/main/resources/asyncapi.yml", "asyncapi: '2.6.0'\nchannels: {}\n")

    contracts = materialize_asyncapi_contracts([_module(svc)])
    openapi = materialize_openapi_contracts(
        [_module(svc, openapi_files=("src/main/resources/openapi.yml",))]
    )

    assert [contract["path"] for contract in contracts] == ["src/main/resources/asyncapi.yml"]
    assert openapi == []


def test_a_yaml_date_key_is_persisted_as_its_iso_text(tmp_path: Path) -> None:
    """Review P4: a date key raised ``TypeError`` in the JSON projection."""
    svc = tmp_path / "svc"
    _write(
        svc / "src/main/resources/asyncapi.yml",
        "asyncapi: '2.6.0'\ninfo: {title: t, version: '1'}\n"
        "x-changelog:\n  2024-01-01: initial\n  3: third\n",
    )
    _write(
        svc / "src/main/resources/openapi.yml",
        "openapi: 3.0.0\nx-changelog:\n  2024-01-01: initial\n",
    )

    contracts = materialize_asyncapi_contracts([_module(svc)])
    openapi = materialize_openapi_contracts(
        [_module(svc, openapi_files=("src/main/resources/openapi.yml",))]
    )

    assert contracts[0]["spec"]["x-changelog"] == {"2024-01-01": "initial", "3": "third"}  # type: ignore[index]
    assert openapi[0]["spec"]["x-changelog"] == {"2024-01-01": "initial"}  # type: ignore[index]


def test_a_mongo_relation_path_is_relative_to_the_indexed_root(tmp_path: Path) -> None:
    """Review P1: a nested module persisted ``src/main/java/...`` (invariant 1)."""
    clear_analysis_caches()
    svc = tmp_path / "apps" / "svc"
    _write(svc / "pom.xml", POM.format(name="svc", deps=""))
    _write(svc / "src/main/java/com/a/OrderStore.java", """package com.a;
import org.springframework.data.mongodb.core.mapping.Document;
import org.springframework.data.mongodb.repository.MongoRepository;
@Document(collection = "orders") class Order { String id; }
interface OrderRepository extends MongoRepository<Order, String> {}
class OrderStore {
  OrderRepository orderRepository;
  void save(Order order) { orderRepository.save(order); }
}
""")
    modules = discover_modules(tmp_path)

    relations = build_architecture_relations(modules, [], [], repo_root=tmp_path)

    writes = [r for r in relations if r.relation == "writes" and r.target_kind == "collection"]
    expected = "apps/svc/src/main/java/com/a/OrderStore.java"
    assert writes and {relation.path for relation in writes} == {expected}
    assert {operation.path for operation in modules[0].data_operations} == {expected}


def test_an_asyncapi_document_outside_the_inventory_is_no_contract(tmp_path: Path) -> None:
    """Review P2: test sources and excluded paths became persisted contracts."""
    document = "asyncapi: '2.6.0'\ninfo: {title: t, version: '1'}\nchannels: {}\n"
    _write(tmp_path / "svc/src/test/resources/asyncapi.yml", document)
    _write(tmp_path / "svc/docs/old/asyncapi.yml", document)
    _write(tmp_path / "svc/src/main/resources/asyncapi.yml", document)
    module = _module(tmp_path / "svc")

    assert [contract["path"] for contract in materialize_asyncapi_contracts([module])] == [
        "docs/old/asyncapi.yml", "src/main/resources/asyncapi.yml",
    ]
    contracts = materialize_asyncapi_contracts(
        [module], repo_root=tmp_path,
        indexed_paths={"svc/src/main/resources/asyncapi.yml"},
    )
    assert [contract["path"] for contract in contracts] == ["src/main/resources/asyncapi.yml"]


def test_an_explicit_import_of_an_absent_class_keeps_the_message_type_unresolved(
    tmp_path: Path,
) -> None:
    """Review P5: ``com.external.OrderEvent`` became ``com.a.OrderEvent``."""
    svc = tmp_path / "svc"
    _write(svc / "src/main/java/com/a/OrderEvent.java",
           "package com.a; public class OrderEvent { String internalOnly; }")
    _write(svc / "src/main/java/com/b/Publisher.java",
           "package com.b; import com.external.OrderEvent; public class Publisher {}")
    endpoint = MessageEndpoint(
        id="produce:OrderEvent", role="produce", system="kafka", topic="orders",
        topic_dynamic=False, source="code", framework="spring-kafka",
        path="svc/src/main/java/com/b/Publisher.java", start_line=1, end_line=1, snippet="",
        module="svc", qualified_name="com.b.Publisher", message_type="OrderEvent",
    )

    definitions = materialize_kafka_dto_definitions({"svc": [endpoint]}, [_module(svc)])

    assert [definition["id"] for definition in definitions] == ["unresolved:OrderEvent"]
    assert definitions[0]["fields"] == []


def test_a_class_level_kafka_listener_is_owned_by_its_handler_methods(tmp_path: Path) -> None:
    """Review P6: the consumer was in the topology but started no flow."""
    clear_analysis_caches()
    svc = tmp_path / "svc"
    _write(svc / "pom.xml", POM.format(name="svc", deps=""))
    rel = "svc/src/main/java/com/a/Handler.java"
    _write(tmp_path / rel, """package com.a;
import org.springframework.kafka.annotation.KafkaHandler;
import org.springframework.kafka.annotation.KafkaListener;
@KafkaListener(topics = "orders")
class Handler {
  @KafkaHandler
  void onOrder(Order order) { kafkaTemplate.send("out", order); }
  @KafkaHandler
  void onRefund(Refund refund) { }
  void helper() { }
  KafkaTemplate<String, Order> kafkaTemplate;
}
""")
    modules = discover_modules(tmp_path)
    endpoints = infer_kafka_endpoints(tmp_path, [rel])
    consume = [endpoint for endpoint in endpoints if endpoint.role == "consume"]
    assert len(consume) == 1 and consume[0].topic == "orders"

    methods = materialize_integration_methods(tmp_path, endpoints, [rel], modules)

    owners = sorted(
        method.qualified_method.rsplit(".", 1)[-1] for method in methods
        if consume[0].id in method.input_endpoint_ids
    )
    assert owners == ["onOrder", "onRefund"]


def test_a_class_level_kafka_listener_with_one_handler_takes_its_payload_type(
    tmp_path: Path,
) -> None:
    """Review P6: tree-sitter nodes compare by value, never by identity.

    ``enclosing(member) is declaration`` was always false, so the single
    ``@KafkaHandler`` was never found and the payload type stayed unknown.
    """
    clear_analysis_caches()
    rel = "svc/src/main/java/com/a/Handler.java"
    _write(tmp_path / "svc/pom.xml", POM.format(name="svc", deps=""))
    _write(tmp_path / rel, """package com.a;
import org.springframework.kafka.annotation.KafkaHandler;
import org.springframework.kafka.annotation.KafkaListener;
@KafkaListener(topics = "orders")
class Handler {
  @KafkaHandler
  void onOrder(Order order) { }
}
""")

    consume = [e for e in infer_kafka_endpoints(tmp_path, [rel]) if e.role == "consume"]

    assert [endpoint.message_type for endpoint in consume] == ["Order"]


def test_a_spring_data_rest_default_path_stays_dynamic(tmp_path: Path) -> None:
    """Review P9: ``Child`` was served at ``/childs`` as a static route.

    Spring's inflector knows irregular plurals (``/children``,
    ``/analyses``); SystemLens keeps the regular spelling as a dynamic
    route instead of guessing (PDR-12). An explicit path stays static.
    """
    clear_analysis_caches()
    deps = (
        "<dependencies><dependency><groupId>org.springframework.boot</groupId>"
        "<artifactId>spring-boot-starter-data-rest</artifactId></dependency></dependencies>"
    )
    _write(tmp_path / "svc/pom.xml", POM.format(name="svc", deps=deps))
    rel = "svc/src/main/java/com/a/Repos.java"
    _write(tmp_path / rel, """package com.a;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.rest.core.annotation.RepositoryRestResource;
interface ChildRepository extends JpaRepository<Child, Long> {
  java.util.List<Child> findByName(String name);
}
interface AnalysisRepository extends JpaRepository<Analysis, Long> {}
@RepositoryRestResource(path = "kids")
interface KidRepository extends JpaRepository<Kid, Long> {}
""")

    endpoints = [
        endpoint for endpoint in infer_framework_endpoints(tmp_path, [rel])
        if endpoint.framework == "spring-data-rest"
    ]

    dynamic = {endpoint.topic for endpoint in endpoints if endpoint.topic_dynamic}
    static = {endpoint.topic for endpoint in endpoints if not endpoint.topic_dynamic}
    assert {"GET /childs", "GET /analysises", "GET /childs/search/findByName"} <= dynamic
    assert {"GET /kids", "GET /kids/{id}"} <= static
    assert not any(topic.startswith(("GET /childs", "GET /analysises")) for topic in static)


def test_an_index_persists_no_asyncapi_contract_from_an_excluded_path(tmp_path: Path) -> None:
    """Review P2 through ``index_repo``: the service passes the inventory."""
    document = "asyncapi: '2.6.0'\ninfo: {title: t, version: '1'}\nchannels: {}\n"
    repo = tmp_path / "repo"
    _write(repo / "svc/pom.xml", POM.format(name="svc", deps=""))
    _write(repo / "svc/src/main/resources/asyncapi.yml", document)
    _write(repo / "svc/src/test/resources/asyncapi.yml", document)
    _write(repo / "svc/legacy/asyncapi.yml", document)
    config = Config()
    config.exclude = [*config.exclude, "svc/legacy/**"]

    with Store(repo) as store:
        index_repo(repo, config, store, full=True)
        contracts = store.all_asyncapi_contracts()

    assert [contract["path"] for contract in contracts] == ["src/main/resources/asyncapi.yml"]


def test_the_inventory_and_the_engine_projection_skip_the_same_out_directory(
    tmp_path: Path,
) -> None:
    """Review P7: ``module/out/`` was extracted but never projected.

    Both now treat a module-rooted ``out`` as build output; a package named
    ``out`` below ``src`` stays source for both.
    """
    _write(tmp_path / "svc/pom.xml", POM.format(name="svc", deps=""))
    _write(tmp_path / "svc/out/gen/com/a/Generated.java", "package com.a; class Generated {}")
    _write(tmp_path / "svc/src/main/java/com/a/out/Kept.java", "package com.a.out; class Kept {}")

    files = list_repo_files(tmp_path, Config())
    projection = tmp_path / "projection"
    copied = project_java_sources(tmp_path, projection, allowed_paths=list(files))

    java = sorted(path for path in files if path.endswith(".java"))
    assert java == ["svc/src/main/java/com/a/out/Kept.java"]
    assert copied == 1 and (projection / java[0]).is_file()


def test_a_config_server_file_change_rescans_the_application_it_configures(
    tmp_path: Path,
) -> None:
    """Review P8: the consumer's ``${app.topic}`` stayed stale until ``--full``."""
    repo = tmp_path / "repo"
    _write(repo / "config-service/pom.xml", POM.format(name="config-service", deps=""))
    config_file = repo / "config-service/src/main/resources/configurations/orders.yml"
    _write(config_file, "app:\n  topic: orders-v1\n")
    _write(repo / "orders/pom.xml", POM.format(name="orders", deps=""))
    _write(repo / "orders/src/main/resources/application.yml",
           "spring:\n  application:\n    name: orders\n")
    _write(repo / "orders/src/main/java/com/a/Listener.java", """package com.a;
import org.springframework.kafka.annotation.KafkaListener;
class Listener {
  @KafkaListener(topics = "${app.topic}")
  void onOrder(String payload) {}
}
""")

    with Store(repo) as store:
        index_repo(repo, Config(), store)
        first = {(e.topic, e.topic_dynamic) for e in store.all_endpoints() if e.system == "kafka"}
        _write(config_file, "app:\n  topic: orders-v2\n")
        index_repo(repo, Config(), store)
        second = {(e.topic, e.topic_dynamic) for e in store.all_endpoints() if e.system == "kafka"}

    assert first == {("orders-v1", False)}
    assert second == {("orders-v2", False)}
