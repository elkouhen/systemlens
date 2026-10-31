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


# --- Typed extraction: repositories, derived queries, EntityManager, JDBC ---

from systemlens.discovery.build.data_operations import discover_data_operations  # noqa: E402
from systemlens.discovery.java import parser as java_parser  # noqa: E402
from systemlens.domain.module_inventory import JpaEntity  # noqa: E402


def _parsed(sources: dict[str, str]) -> list[tuple[str, bytes, object]]:
    parser = java_parser.java_parser("tests")
    files = []
    for path, text in sources.items():
        source = text.encode("utf-8")
        files.append((path, source, parser.parse(source).root_node))
    return files


def _entities(*names: str) -> tuple[JpaEntity, ...]:
    return tuple(JpaEntity(name, f"src/{name.rsplit('.', 1)[-1]}.java", 1) for name in names)


_ORDER_ENTITY = (
    "package shop;\nimport jakarta.persistence.Entity;\n@Entity public class Order { }\n"
)


def _facts(operations) -> list[tuple[object, ...]]:
    return [
        (item.owner_method, item.operation, item.action, item.resource, item.key_expression,
         item.key_property, item.unresolved_reason)
        for item in operations
    ]


def test_constructor_injected_crud_repository_matches_a_jpa_repository_field() -> None:
    files = _parsed({
        "src/Order.java": _ORDER_ENTITY,
        "src/OrderRepository.java": (
            "package shop;\nimport org.springframework.data.repository.CrudRepository;\n"
            "import java.util.UUID;\n"
            "public interface OrderRepository extends CrudRepository<Order, UUID> {}\n"
        ),
        "src/OtherRepository.java": (
            "package shop;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface OtherRepository extends JpaRepository<Order, String> {}\n"
        ),
        "src/Service.java": (
            "package shop;\nclass Service {\n"
            "  private final OrderRepository orders;\n"
            "  private OtherRepository other;\n"
            "  Service(OrderRepository orders) { this.orders = orders; }\n"
            "  void a(String id) { orders.findById(id); }\n"
            "  void b(String id) { other.findById(id); }\n"
            "  void c(OrderRepository injected, String id) { injected.deleteById(id); }\n"
            "}\n"
        ),
    })

    operations = discover_data_operations("shop", files, _entities("shop.Order"))

    assert _facts(operations) == [
        ("a", "findById", "read", "shop.Order", "id", None, None),
        ("b", "findById", "read", "shop.Order", "id", None, None),
        ("c", "deleteById", "delete", "shop.Order", "id", None, None),
    ]
    assert {item.resource_identity for item in operations} == {"jpa:shop:shop.Order"}
    assert all(item.key_identity is None for item in operations)


def test_repository_inheritance_through_a_module_interface_resolves_the_entity() -> None:
    files = _parsed({
        "src/BaseRepository.java": (
            "package shop;\nimport org.springframework.data.repository.NoRepositoryBean;\n"
            "import org.springframework.data.repository.ListCrudRepository;\n"
            "import org.springframework.data.jpa.repository.JpaSpecificationExecutor;\n"
            "@NoRepositoryBean public interface BaseRepository<T, K> extends "
            "JpaSpecificationExecutor<T>, ListCrudRepository<T, K> {}\n"
        ),
        "src/OrderRepository.java": (
            "package shop;\npublic interface OrderRepository extends BaseRepository<Order, Long> {}\n"
        ),
        "src/Service.java": (
            "package shop;\nclass Service { OrderRepository repo; "
            "void run() { repo.save(new Order()); } }\n"
        ),
    })

    operations = discover_data_operations("shop", files, _entities("shop.Order"))

    assert _facts(operations) == [("run", "save", "create_or_update", "shop.Order", None, None, None)]


def test_ambiguous_entity_yields_an_unresolved_fact_not_a_guess() -> None:
    files = _parsed({
        "src/OrderRepository.java": (
            "package shop;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface OrderRepository extends JpaRepository<Order, String> {}\n"
        ),
        "src/Service.java": (
            "package shop;\nclass Service { OrderRepository repo; "
            "void run() { repo.findAll(); } }\n"
        ),
    })

    operations = discover_data_operations(
        "shop", files, _entities("billing.Order", "sales.Order"),
    )

    [operation] = operations
    assert operation.action == "read"
    assert operation.resource is None and operation.resource_identity is None
    assert operation.unresolved_reason == "entity Order is ambiguous: billing.Order, sales.Order"
    assert operation.coverage == "partial"
    assert operation.confidence == "low"


def test_a_save_on_a_non_repository_field_or_shadowed_name_yields_nothing() -> None:
    files = _parsed({
        "src/OrderRepository.java": (
            "package shop;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface OrderRepository extends JpaRepository<Order, String> {}\n"
        ),
        "src/Service.java": (
            "package shop;\nclass Service {\n"
            "  OrderRepository repo; Cache cache;\n"
            "  void run() { cache.save(\"x\"); }\n"
            "  void shadow(String repo) { repo.save(\"x\"); }\n"
            "  void local() { Cache repo = new Cache(); repo.save(\"x\"); }\n"
            "}\n"
        ),
    })

    assert discover_data_operations("shop", files, _entities("shop.Order")) == ()


def test_derived_queries_and_literal_query_annotations_classify() -> None:
    files = _parsed({
        "src/OrderRepository.java": (
            "package shop;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
            "import org.springframework.data.jpa.repository.Modifying;\n"
            "import org.springframework.data.jpa.repository.Query;\n"
            "import java.util.List;\n"
            "public interface OrderRepository extends JpaRepository<Order, String> {\n"
            "  List<Order> findByCustomerId(String customerId);\n"
            "  void deleteByStatus(String status);\n"
            "  @Modifying @Query(\"update Order o set o.status = :s where o.id = :id\") int close(String s, String id);\n"
            "  @Query(\"update Order o set o.status = :s\") int closeAll(String s);\n"
            "  @Query(\"select o from Order o where o.total > :min\") List<Order> big(long min);\n"
            "  @Query(value = \"select * from orders\", nativeQuery = true) List<Order> raw();\n"
            "  List<Order> customThing(String x);\n"
            "  List<Order> findAll(Specification<Order> spec);\n"
            "  @Query(Queries.SQL) List<Order> constant();\n"
            "}\n"
        ),
        "src/Service.java": (
            "package shop;\nclass Service { OrderRepository repo;\n"
            "  void run(String id, String status) {\n"
            "    repo.findByCustomerId(id);\n    repo.deleteByStatus(status);\n"
            "    repo.close(\"done\", id);\n    repo.closeAll(\"done\");\n    repo.big(10L);\n"
            "    repo.raw();\n    repo.customThing(id);\n    repo.findAll(spec);\n    repo.constant();\n"
            "    repo.findByCustomerId(\"c-1\");\n"
            "  }\n}\n"
        ),
    })

    operations = discover_data_operations("shop", files, _entities("shop.Order"))

    assert _facts(operations) == [
        ("run", "findByCustomerId", "read", "shop.Order", "id", "customerId", None),
        ("run", "deleteByStatus", "delete", "shop.Order", "status", "status", None),
        ("run", "close", "update", "shop.Order", None, None, None),
        ("run", "closeAll", "unknown", "shop.Order", None, None, "update query without @Modifying"),
        ("run", "big", "read", "shop.Order", None, None, None),
        ("run", "raw", "read", "orders", None, None, "native query: resource kept on the JDBC table"),
        ("run", "customThing", "unknown", "shop.Order", None, None,
         "method name outside the Spring Data derived-query grammar"),
        ("run", "findAll", "unknown", "shop.Order", None, None,
         "query built from a Specification argument"),
        ("run", "constant", "unknown", "shop.Order", None, None, "@Query string is not a literal"),
        ("run", "findByCustomerId", "read", "shop.Order", '"c-1"', "customerId", None),
    ]
    by_line = {item.line: item for item in operations}
    assert by_line[13].key_identity == 'jpa:shop:shop.Order#customerId:literal:"c-1"'
    assert by_line[4].key_identity is None
    assert by_line[9].resource_identity == "jdbc:shop:orders"
    assert [item.coverage for item in operations][:4] == ["supported", "supported", "supported", "partial"]


def test_entity_manager_operations_classify_arguments_and_literal_queries() -> None:
    files = _parsed({
        "src/Dao.java": (
            "package shop;\nimport jakarta.persistence.EntityManager;\n"
            "class Dao {\n  private EntityManager entityManager;\n"
            "  void run(String id, String sql, Order existing) {\n"
            "    entityManager.find(Order.class, id);\n"
            "    entityManager.persist(new Order());\n"
            "    entityManager.merge(existing);\n"
            "    entityManager.remove(existing);\n"
            "    entityManager.getReference(Order.class, \"o-1\");\n"
            "    entityManager.createQuery(sql);\n"
            "    entityManager.createQuery(\"delete from Order o where o.closed = true\");\n"
            "    entityManager.createNativeQuery(\"UPDATE orders SET x = 1 WHERE id = ?\");\n"
            "    entityManager.createNamedQuery(\"Order.all\");\n"
            "    entityManager.persist(build());\n"
            "  }\n}\n"
        ),
    })

    operations = discover_data_operations("shop", files, _entities("shop.Order"))

    assert _facts(operations) == [
        ("run", "find", "read", "shop.Order", "id", None, None),
        ("run", "persist", "insert", "shop.Order", None, None, None),
        ("run", "merge", "create_or_update", "shop.Order", None, None, None),
        ("run", "remove", "delete", "shop.Order", None, None, None),
        ("run", "getReference", "read", "shop.Order", '"o-1"', None, None),
        ("run", "createQuery", "unknown", None, None, None,
         "SQL string is not a literal or a same-class constant"),
        ("run", "createQuery", "delete", "shop.Order", None, None, None),
        ("run", "createNativeQuery", "update", "orders", None, None, None),
        ("run", "createNamedQuery", "unknown", None, None, None, "named query not resolved"),
        ("run", "persist", "insert", None, None, None,
         "entity type of the argument not established"),
    ]
    assert operations[4].key_identity == 'jpa:shop:shop.Order:literal:"o-1"'
    assert operations[7].resource_identity == "jdbc:shop:orders"


def test_literal_jdbc_statements_classify_single_table_statements_only() -> None:
    files = _parsed({
        "src/Dao.java": (
            "package shop;\nimport org.springframework.jdbc.core.JdbcTemplate;\n"
            "class Dao {\n"
            "  private static final String COUNT = \"SELECT count(*) FROM orders WHERE id = ?\";\n"
            "  private final JdbcTemplate jdbc;\n"
            "  Dao(JdbcTemplate jdbc) { this.jdbc = jdbc; }\n"
            "  void run(String id) {\n"
            "    jdbc.update(\"DELETE FROM orders WHERE id = ?\", id);\n"
            "    jdbc.query(\"SELECT o.id FROM orders o JOIN lines l ON l.order_id = o.id\", mapper);\n"
            "    jdbc.update(buildSql(), id);\n"
            "    jdbc.queryForObject(COUNT, Long.class, id);\n"
            "    jdbc.update(\"UPDATE orders SET qty = ? WHERE id = ? AND version = ?\", 1, id, 3);\n"
            "    jdbc.update(\"INSERT INTO orders (id) VALUES (?)\", id);\n"
            "    jdbc.update(\"DELETE FROM a; DELETE FROM b\");\n"
            "    jdbc.execute(\"CALL rebuild()\");\n"
            "    jdbc.update(\"DELETE FROM orders WHERE id = \" + id);\n"
            "    jdbc.queryForList(\"\"\"\n        SELECT * FROM lines\n        WHERE sku = ?\"\"\", sku);\n"
            "    jdbc.getDataSource();\n"
            "  }\n}\n"
        ),
    })

    operations = discover_data_operations("shop", files, ())

    assert _facts(operations) == [
        ("run", "update", "delete", "orders", "id", None, None),
        ("run", "query", "read", None, None, None, "join or subquery: several tables involved"),
        ("run", "update", "unknown", None, None, None, "SQL string is built or formatted at runtime"),
        ("run", "queryForObject", "read", "orders", "id", None, None),
        ("run", "update", "update", "orders", None, None, None),
        ("run", "update", "insert", "orders", None, None, None),
        ("run", "update", "unknown", None, None, None, "multiple SQL statements"),
        ("run", "execute", "unknown", None, None, None, "stored procedure"),
        ("run", "update", "unknown", None, None, None, "SQL string is concatenated"),
        ("run", "queryForList", "read", "lines", "sku", "sku", None),
    ]
    assert operations[0].resource_identity == "jdbc:shop:orders"
    assert operations[0].technology == "jdbc"
    assert operations[4].guard == "conditional_update"
    assert operations[4].guard_scope == "operation"
    assert operations[0].guard is None
    assert [item.coverage for item in operations][:3] == ["supported", "partial", "unsupported"]


# --- Transaction scope and conflict-control evidence ------------------------


def _repository_sources(service_body: str, entity_fields: str = "@Id private String id;") -> dict[str, str]:
    return {
        "src/Order.java": (
            "package shop;\nimport jakarta.persistence.Entity;\nimport jakarta.persistence.Id;\n"
            "import jakarta.persistence.Version;\n"
            f"@Entity public class Order {{ {entity_fields} }}\n"
        ),
        "src/OrderRepository.java": (
            "package shop;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface OrderRepository extends JpaRepository<Order, String> {}\n"
        ),
        "src/Service.java": (
            "package shop;\nimport org.springframework.transaction.annotation.Transactional;\n"
            "import org.springframework.transaction.annotation.Isolation;\n"
            "import org.springframework.transaction.annotation.Propagation;\n"
            "import org.springframework.transaction.support.TransactionTemplate;\n"
            f"{service_body}\n"
        ),
    }


def test_operations_of_one_transactional_method_share_a_scope_and_helpers_do_not() -> None:
    files = _parsed(_repository_sources(
        "class Service {\n  OrderRepository repo; TransactionTemplate tx;\n"
        "  @Transactional void move(String id) {\n"
        "    repo.findById(id);\n    repo.save(new Order());\n    helper(id);\n  }\n"
        "  void helper(String id) { repo.deleteById(id); }\n"
        "  @Transactional(isolation = Isolation.READ_UNCOMMITTED, propagation = Propagation.REQUIRES_NEW, readOnly = true)\n"
        "  void peek(String id) { repo.findById(id); }\n"
        "  void template(String id) { tx.execute(status -> { repo.save(new Order()); return null; }); }\n"
        "}"
    ))

    operations = discover_data_operations("shop", files, _entities("shop.Order"))

    by_method = {}
    for item in operations:
        by_method.setdefault(item.owner_method, []).append(item)
    move = by_method["move"]
    assert len({item.transaction_id for item in move}) == 1 and move[0].transaction_id
    assert (move[0].propagation, move[0].isolation, move[0].read_only) == ("REQUIRED", None, False)
    [helper] = by_method["helper"]
    assert helper.transaction_id is None and helper.propagation is None
    [peek] = by_method["peek"]
    assert (peek.propagation, peek.isolation, peek.read_only) == ("REQUIRES_NEW", "read_uncommitted", True)
    assert peek.transaction_id != move[0].transaction_id
    [template] = by_method["template"]
    assert template.transaction_id and template.transaction_id != move[0].transaction_id
    assert template.propagation is None and template.isolation is None


def test_class_level_transactional_is_inherited_by_methods_without_one() -> None:
    files = _parsed(_repository_sources(
        "@Transactional(readOnly = true)\nclass Service {\n  OrderRepository repo;\n"
        "  void load(String id) { repo.findById(id); }\n"
        "  @Transactional void store() { repo.save(new Order()); }\n}"
    ))

    operations = discover_data_operations("shop", files, _entities("shop.Order"))

    load, store = operations
    assert load.transaction_id and load.read_only is True
    assert store.transaction_id and store.read_only is False
    assert load.transaction_id != store.transaction_id


def test_isolation_evidence_reaches_the_flow_step_unchanged(tmp_path: Path) -> None:
    _write(tmp_path / "pom.xml", (
        "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
        "<artifactId>shop</artifactId><version>1.0</version></project>"
    ))
    for path, text in _repository_sources(
        "import org.springframework.web.bind.annotation.*;\n@RestController class Service {\n"
        "  OrderRepository repo;\n"
        "  @Transactional(isolation = Isolation.READ_UNCOMMITTED)\n"
        "  @GetMapping(\"/orders/{id}\") Order get(@PathVariable String id) { return repo.findById(id).get(); }\n}"
    ).items():
        _write(tmp_path / "src" / "main" / "java" / "shop" / Path(path).name, text)

    with Store(tmp_path) as store:
        index_repo(tmp_path, CONFIG, store, full=True)
        [flow] = [flow for flow in store.all_code_flows() if flow.steps[-1].operation_id]
    step = flow.steps[-1]
    assert step.isolation == "read_uncommitted"
    assert step.transaction_id is not None
    assert step.guard is None


def test_version_field_marks_every_write_on_the_entity_and_only_writes() -> None:
    body = (
        "class Service {\n  OrderRepository repo;\n"
        "  void run(String id) {\n    repo.findById(id);\n    repo.save(new Order());\n    repo.deleteById(id);\n  }\n}"
    )
    with_version = discover_data_operations(
        "shop", _parsed(_repository_sources(body, "@Id private String id; @Version private long version;")),
        (JpaEntity("shop.Order", "src/Order.java", 1, version_field="version"),),
    )
    without_version = discover_data_operations(
        "shop", _parsed(_repository_sources(body)), _entities("shop.Order"),
    )

    assert [(item.operation, item.guard, item.guard_scope) for item in with_version] == [
        ("findById", None, None),
        ("save", "optimistic_version", "resource"),
        ("deleteById", "optimistic_version", "resource"),
    ]
    assert all(item.guard is None for item in without_version)


def test_jpa_entity_inventory_records_the_version_field(tmp_path: Path) -> None:
    _write(tmp_path / "pom.xml", (
        "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
        "<artifactId>shop</artifactId><version>1.0</version></project>"
    ))
    _write(tmp_path / "src" / "main" / "java" / "shop" / "Order.java", (
        "package shop;\nimport jakarta.persistence.*;\n"
        "@Entity public class Order { @Id private String id; @Version private Long revision; }\n"
    ))
    with Store(tmp_path) as store:
        index_repo(tmp_path, CONFIG, store, full=True)
        [entity] = store.all_modules()[0].jpa_entities
    assert entity.version_field == "revision"
    assert [field.name for field in entity.fields] == ["id", "revision"]


def test_pessimistic_lock_on_the_repository_method_is_an_operation_guard() -> None:
    files = _parsed({
        "src/OrderRepository.java": (
            "package shop;\nimport jakarta.persistence.LockModeType;\n"
            "import org.springframework.data.jpa.repository.JpaRepository;\n"
            "import org.springframework.data.jpa.repository.Lock;\nimport java.util.Optional;\n"
            "public interface OrderRepository extends JpaRepository<Order, String> {\n"
            "  @Override @Lock(LockModeType.PESSIMISTIC_WRITE) Optional<Order> findById(String id);\n"
            "  @Lock(LockModeType.OPTIMISTIC) Optional<Order> findByCode(String code);\n}\n"
        ),
        "src/Service.java": (
            "package shop;\nclass Service { OrderRepository repo;\n"
            "  void run(String id) {\n    repo.findById(id);\n    repo.findByCode(id);\n    repo.save(new Order());\n  } }\n"
        ),
    })

    operations = discover_data_operations("shop", files, _entities("shop.Order"))

    assert [(item.operation, item.guard, item.guard_scope) for item in operations] == [
        ("findById", "pessimistic_lock", "operation"),
        ("findByCode", None, None),
        ("save", None, None),
    ]


def test_mongo_atomic_updates_are_recognised_from_the_invocation_only() -> None:
    from systemlens.domain.data_operation_projection import mongo_atomic_guard

    assert mongo_atomic_guard("findAndModify", "template.findAndModify(q, u, Order.class)") == "atomic_update"
    assert mongo_atomic_guard("upsert", None) == "atomic_update"
    assert mongo_atomic_guard(
        "updateFirst", 'template.updateFirst(query(where("id").is(id)), new Update().inc("qty", -1), Order.class)',
    ) == "atomic_update"
    assert mongo_atomic_guard(
        "updateFirst", 'template.updateFirst(query(where("id").is(id)), new Update().set("qty", 3), Order.class)',
    ) is None
    assert mongo_atomic_guard(
        "updateFirst",
        'template.updateFirst(query(where("id").is(id).and("version").is(v)), new Update().set("qty", 3), Order.class)',
    ) == "atomic_update"
    assert mongo_atomic_guard("updateFirst", "template.updateFirst(query, update, Order.class)") is None
    assert mongo_atomic_guard("save", "repo.save(doc)") is None
