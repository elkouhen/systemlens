"""Persisted data operations: table, incremental refresh, and flow projection."""

import shutil
from pathlib import Path

from systemlens.application.flow_calculation import calculate_persisted_flows
from systemlens.domain.data_operation_projection import data_operations_from_modules
from systemlens.domain.data_operations import DataOperation, compute_data_operation_id
from systemlens.indexing.code_flows import materialize_code_flows
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.sqlite import SCHEMA_VERSION, Store

CONFIG = Config(codeql_enabled=False, call_graph_engine="none")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _jpa_repository(root: Path) -> Path:
    """A Maven module with one entity, one repository, and one REST controller."""
    _write(root / "pom.xml", (
        "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
        "<artifactId>orders</artifactId><version>1.0</version></project>"
    ))
    source = root / "src" / "main" / "java" / "example"
    _write(source / "Product.java", (
        "package example;\nimport jakarta.persistence.Entity;\nimport jakarta.persistence.Id;\n"
        "@Entity public class Product { @Id private String id; private int stock; }\n"
    ))
    _write(source / "ProductRepository.java", (
        "package example;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
        "public interface ProductRepository extends JpaRepository<Product, String> {}\n"
    ))
    _write(source / "ProductController.java", (
        "package example;\n"
        "import org.springframework.web.bind.annotation.*;\n"
        "@RestController\npublic class ProductController {\n"
        "  private final ProductRepository repository;\n"
        "  ProductController(ProductRepository repository) { this.repository = repository; }\n"
        "  @GetMapping(\"/products/{id}\")\n"
        "  public Product read(@PathVariable String id) {\n"
        "    return repository.findById(id).orElseThrow();\n"
        "  }\n"
        "  @PostMapping(\"/products\")\n"
        "  public Product create(@RequestBody Product product) {\n"
        "    return repository.save(product);\n"
        "  }\n"
        "}\n"
    ))
    return source / "ProductController.java"


def test_index_persists_operations_with_relative_paths_and_stable_ids(tmp_path: Path) -> None:
    controller = _jpa_repository(tmp_path)

    with Store(tmp_path) as store:
        index_repo(tmp_path, CONFIG, store, full=True)
        first = store.all_data_operations()
        index_repo(tmp_path, CONFIG, store, full=True)
        second = store.all_data_operations()

    assert [(item.technology, item.operation, item.action, item.owner_method) for item in first] == [
        ("jpa", "findById", "read", "read"),
        ("jpa", "save", "create_or_update", "create"),
    ]
    assert {item.path for item in first} == {controller.relative_to(tmp_path).as_posix()}
    assert all(not Path(item.path).is_absolute() for item in first)
    assert first[0].resource == "example.Product"
    assert first[0].resource_identity == "jpa:orders:example.Product"
    assert first[0].key_expression == "id"
    assert first[0].reads and not first[0].writes
    assert first[1].writes and not first[1].reads
    assert [item.id for item in first] == [item.id for item in second]
    assert first[0].id == compute_data_operation_id(
        "orders", first[0].path, "read", first[0].line, "jpa", "findById",
    )


def test_deleting_a_source_file_removes_its_operations_on_reindex(tmp_path: Path) -> None:
    controller = _jpa_repository(tmp_path)

    with Store(tmp_path) as store:
        index_repo(tmp_path, CONFIG, store, full=True)
        assert len(store.all_data_operations()) == 2
        controller.unlink()
        index_repo(tmp_path, CONFIG, store)
        assert store.all_data_operations() == []


def test_older_index_without_the_table_opens_and_reindexes(tmp_path: Path) -> None:
    _jpa_repository(tmp_path)
    with Store(tmp_path) as store:
        index_repo(tmp_path, CONFIG, store, full=True)
        store.conn.execute("DROP TABLE data_operations")
        store.set_meta("schema_version", "38")

    with Store(tmp_path) as store:
        assert store.get_meta("schema_version") == SCHEMA_VERSION
        assert store.all_data_operations() == []
        # The module inventory JSON of the older snapshot still projects.
        projected = data_operations_from_modules(tmp_path, store.all_modules())
        assert [item.operation for item in projected] == ["findById", "save"]
        index_repo(tmp_path, CONFIG, store, full=True)
        assert [item.operation for item in store.all_data_operations()] == ["findById", "save"]


def test_flow_steps_from_the_table_match_the_module_inventory_projection(tmp_path: Path) -> None:
    _jpa_repository(tmp_path)
    with Store(tmp_path) as store:
        index_repo(tmp_path, CONFIG, store, full=True)
        endpoints = store.all_endpoints()
        modules = store.all_modules()
        operations = store.all_data_operations()
        persisted = store.all_code_flows()

    from_table = materialize_code_flows(tmp_path, endpoints, modules, operations)
    from_json = materialize_code_flows(tmp_path, endpoints, modules)

    def shape(flows: list) -> list[tuple[object, ...]]:
        return [
            (flow.id, tuple(
                (step.kind, step.name, step.path, step.start_line, step.operation,
                 step.action, step.resource_identity, step.key, step.operation_id)
                for step in flow.steps
            ))
            for flow in flows
        ]

    assert shape(from_table) == shape(from_json)
    data_steps = [step for flow in persisted for step in flow.steps if step.operation_id]
    assert {step.operation_id for step in data_steps} == {item.id for item in operations}
    assert {step.kind for step in data_steps} == {"data_read", "data_write"}


def test_flows_calculate_reads_the_persisted_table(tmp_path: Path) -> None:
    _jpa_repository(tmp_path)
    with Store(tmp_path) as store:
        index_repo(tmp_path, CONFIG, store, full=True)
        before = {
            (step.operation_id, step.kind)
            for flow in store.all_code_flows() for step in flow.steps if step.operation_id
        }
        calculate_persisted_flows(store)
        after = {
            (step.operation_id, step.kind)
            for flow in store.all_code_flows() for step in flow.steps if step.operation_id
        }
    assert before and before == after


def test_store_round_trips_every_operation_column(tmp_path: Path) -> None:
    operation = DataOperation(
        id="op-1", module="orders", path="src/Dao.java", owner_method="run", line=4,
        end_line=5, technology="jdbc", operation="update", action="delete", reads=False,
        writes=True, resource="orders", resource_identity="jdbc:orders:orders",
        key_expression="id", key_property="id", key_identity=None, confidence="medium",
        unresolved_reason=None, receiver="jdbc", transaction_id="tx-1",
        propagation="REQUIRED", isolation="read_uncommitted", read_only=False,
        guard="conditional_update", guard_scope="operation", value_dependencies=("op-0",),
    )
    with Store(tmp_path) as store:
        store.replace_data_operations([operation])
        assert store.all_data_operations() == [operation]
        store.remove_files(["src/Dao.java"])
        assert store.all_data_operations() == []


def test_lab_copy_is_not_required_for_projection(tmp_path: Path) -> None:
    """The projection never reopens sources: a removed tree still projects."""
    _jpa_repository(tmp_path)
    with Store(tmp_path) as store:
        index_repo(tmp_path, CONFIG, store, full=True)
        modules = store.all_modules()
    shutil.rmtree(tmp_path / "src")
    projected = data_operations_from_modules(tmp_path, modules)
    assert [item.operation for item in projected] == ["findById", "save"]
