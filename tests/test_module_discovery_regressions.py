"""Regression coverage for module discovery and key-identity parameter positions."""
from pathlib import Path

from systemlens.callgraph.model import MethodCall
from systemlens.discovery.build.modules import (
    discover_excluded_module_paths,
    discover_modules,
)
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.indexing.java_value_origins import trace_scalar_key_identity


def _pom(path: Path, artifact: str, packaging: str = "jar") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        f"<groupId>com.acme</groupId><artifactId>{artifact}</artifactId><version>1.0.0</version>"
        f"<packaging>{packaging}</packaging></project>",
        encoding="utf-8",
    )


_SPRING_MAIN = (
    "import org.springframework.boot.SpringApplication;\n"
    "class App { public static void main(String[] a) { SpringApplication.run(App.class, a); } }\n"
)


def test_maven_module_named_attestation_service_is_not_a_test_module(tmp_path: Path) -> None:
    # docs/spec-fonc/modules.md: names such as attestation-service and
    # contest-service remain valid service identities.
    for name in ("attestation-service", "contest-service"):
        _pom(tmp_path / name / "pom.xml", name)
        src = tmp_path / name / "src/main/java/com/acme/App.java"
        src.parent.mkdir(parents=True)
        src.write_text(_SPRING_MAIN, encoding="utf-8")

    names = sorted(module.name for module in discover_modules(tmp_path))
    excluded = discover_excluded_module_paths(tmp_path)

    assert names == ["attestation-service", "contest-service"], names
    assert excluded == (), excluded


def test_maven_module_in_directory_named_latest_is_not_a_test_module(tmp_path: Path) -> None:
    _pom(tmp_path / "latest" / "pom.xml", "orders-api")
    assert [module.name for module in discover_modules(tmp_path)] == ["orders-api"]


def test_gradle_aggregator_does_not_inherit_nested_subproject_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle").write_text("rootProject.name = 'platform'\ninclude 'app'\n")
    (tmp_path / "build.gradle").write_text("subprojects { }\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "build.gradle").write_text("plugins { id 'org.springframework.boot' }\n")
    src = tmp_path / "app/src/main/java/com/acme/App.java"
    src.parent.mkdir(parents=True)
    src.write_text(_SPRING_MAIN, encoding="utf-8")

    by_name = {module.name: module for module in discover_modules(tmp_path)}

    assert by_name["app"].starts_application is True
    assert by_name["platform"].starts_application is False, by_name["platform"]


def test_maven_jar_parent_does_not_inherit_nested_module_entrypoint(tmp_path: Path) -> None:
    # A non-pom parent (unusual but legal) must not become deployable because
    # a nested module owns the Spring Boot main.
    _pom(tmp_path / "pom.xml", "platform", packaging="jar")
    _pom(tmp_path / "app" / "pom.xml", "app")
    src = tmp_path / "app/src/main/java/com/acme/App.java"
    src.parent.mkdir(parents=True)
    src.write_text(_SPRING_MAIN, encoding="utf-8")

    by_name = {module.name: module for module in discover_modules(tmp_path)}

    assert by_name["app"].starts_application is True
    assert by_name["platform"].starts_application is False


def test_key_identity_parameter_index_ignores_comments_in_parameter_list(tmp_path: Path) -> None:
    entry_path = "src/Entry.java"
    adapter_path = "src/Adapter.java"
    (tmp_path / entry_path).parent.mkdir(parents=True)
    (tmp_path / entry_path).write_text(
        "package example; class Entry { void handle(String a, String b, String c) { "
        "new Adapter().load(a, b, c); } }\n",
        encoding="utf-8",
    )
    (tmp_path / adapter_path).write_text(
        "package example; class Adapter { void load(String x, /* key */ String y, String z) { "
        "repository.findById(y); } }\n",
        encoding="utf-8",
    )
    entry = IntegrationMethod("entry-method", "orders", "example.Entry.handle", entry_path, 1, 1, (), ())
    adapter = IntegrationMethod("adapter-method", "orders", "example.Adapter.load", adapter_path, 1, 1, (), ())
    call = MethodCall(
        caller=entry.qualified_method, caller_path=entry.path, caller_line=1,
        callee=adapter.qualified_method, callee_path=adapter.path, callee_line=1, call_line=1,
    )

    identity = trace_scalar_key_identity(
        tmp_path, [(adapter, call, False)], entry, adapter, "y", "jpa:orders:example.Order",
    )

    # ``y`` is the second parameter: the key flows from ``b`` (index 1).
    assert identity == "jpa:orders:example.Order:parameter:entry-method:1", identity


_ENTITY_POM = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE project [<!ENTITY name "evil">]>\n'
    '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
    "<groupId>com.acme</groupId><artifactId>&name;</artifactId><version>1.0.0</version>"
    "<dependencies><dependency><artifactId>orders-api</artifactId></dependency></dependencies>"
    "</project>"
)


def test_pom_with_internal_dtd_entity_is_skipped_like_a_malformed_pom(tmp_path: Path) -> None:
    # Review I10 (2026-11-03): defusedxml refuses entity declarations with
    # EntitiesForbidden, a ValueError that aborted the whole index. The POM is
    # now treated like an unparsable POM; the protection stays on.
    from systemlens.discovery.build.maven import parse_pom, parse_pom_root, pom_version
    from systemlens.discovery.build.modules import maven_module_dependencies
    from systemlens.indexing.service import index_repo
    from systemlens.infrastructure.config import Config
    from systemlens.storage.sqlite import Store

    _pom(tmp_path / "orders" / "pom.xml", "orders-api")
    hostile = tmp_path / "hostile" / "pom.xml"
    hostile.parent.mkdir()
    hostile.write_text(_ENTITY_POM, encoding="utf-8")

    assert parse_pom_root(hostile) is None
    assert parse_pom(hostile) == (None, False, None)
    assert pom_version(hostile) is None
    assert maven_module_dependencies(hostile) == set()

    with Store(tmp_path) as store:
        index_repo(tmp_path, Config(), store, full=True)
        names = [module.name for module in store.all_modules()]

    assert "orders-api" in names
    assert "evil" not in names


# --- Lot 3 of the indexing review (2026-11-03): build ownership (ADR-80) ---


def _app(directory: Path, package: str, cls: str, *, main: bool = True) -> None:
    """Write a Spring controller, with or without a Spring Boot entry point."""
    source = directory / f"src/main/java/com/ex/{package}/{cls}.java"
    source.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        f"  public static void main(String[] a) {{ SpringApplication.run({cls}.class, a); }}\n"
        if main else ""
    )
    source.write_text(
        f"package com.ex.{package};\n"
        "import org.springframework.boot.SpringApplication;\n"
        "import org.springframework.boot.autoconfigure.SpringBootApplication;\n"
        "import org.springframework.web.bind.annotation.*;\n"
        + ("@SpringBootApplication\n" if main else "")
        + "@RestController\n"
        f"public class {cls} {{\n{entry}"
        f'  @GetMapping("/{package}/items") public String items() {{ return "x"; }}\n'
        "}\n",
        encoding="utf-8",
    )


def _feign_client(directory: Path, package: str, target: str) -> None:
    source = directory / f"src/main/java/com/ex/{package}/TargetClient.java"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        f"package com.ex.{package};\n"
        "import org.springframework.cloud.openfeign.FeignClient;\n"
        "import org.springframework.web.bind.annotation.*;\n"
        f'@FeignClient(name = "{target}")\n'
        f'public interface TargetClient {{ @GetMapping("/{target}/items") String items(); }}\n',
        encoding="utf-8",
    )


def _child_pom(path: Path, artifact: str, dependencies: str = "", group: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    group_xml = f"<groupId>{group}</groupId>" if group else ""
    path.write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        "<parent><groupId>com.ex</groupId><artifactId>root</artifactId><version>1.0</version></parent>"
        f"{group_xml}<artifactId>{artifact}</artifactId><dependencies>{dependencies}</dependencies></project>",
        encoding="utf-8",
    )


def _aggregator(path: Path, *modules: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    declared = "".join(f"<module>{module}</module>" for module in modules)
    path.write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        "<groupId>com.ex</groupId><artifactId>root</artifactId><version>1.0</version>"
        f"<packaging>pom</packaging><modules>{declared}</modules></project>",
        encoding="utf-8",
    )


def _index(root: Path, *, full: bool = True) -> dict[str, list[tuple]]:
    from systemlens.indexing.service import index_repo
    from systemlens.infrastructure.config import Config
    from systemlens.storage.sqlite import Store

    with Store(root) as store:
        index_repo(root, Config(), store, full=full)
        conn = store.conn
        return {
            "modules": [tuple(row) for row in conn.execute(
                "SELECT path, identity FROM modules ORDER BY path")],
            "endpoints": [tuple(row) for row in conn.execute(
                "SELECT path, module FROM endpoints ORDER BY path, start_line")],
            "relations": [tuple(row) for row in conn.execute(
                "SELECT source_name, relation, target_name, module FROM architecture_relations "
                "ORDER BY 1, 2, 3")],
            "dependencies": [tuple(row) for row in conn.execute(
                "SELECT source, target FROM module_dependencies ORDER BY 1, 2")],
            "flow_modules": [row[0] for row in conn.execute(
                "SELECT DISTINCT module FROM code_flows")],
            "method_modules": [row[0] for row in conn.execute(
                "SELECT DISTINCT module FROM integration_methods")],
        }


def _gradle_shop(root: Path, *, client_main: bool) -> None:
    """Probe p2/p2b: a Spring Boot root project with three subprojects."""
    (root / "settings.gradle").write_text(
        "rootProject.name = 'shop'\ninclude 'orders', 'orders-client', 'billing'\n",
        encoding="utf-8",
    )
    (root / "build.gradle").write_text("\n", encoding="utf-8")
    for name in ("orders", "orders-client", "billing"):
        (root / name).mkdir()
    (root / "orders/build.gradle").write_text(
        "dependencies { implementation project(':orders-client')\n"
        " testImplementation project(':billing') }\n",
        encoding="utf-8",
    )
    (root / "orders-client/build.gradle").write_text("\n", encoding="utf-8")
    (root / "billing/build.gradle").write_text("\n", encoding="utf-8")
    _app(root, "gw", "GatewayApp")
    _app(root / "orders", "orders", "OrdersApp")
    _app(root / "billing", "billing", "BillingApp")
    if client_main:
        _app(root / "orders-client", "oc", "ClientApp")
    _feign_client(root / "orders-client", "oc", "billing")


def test_gradle_file_belongs_to_its_deepest_project_not_the_root_application(
    tmp_path: Path,
) -> None:
    # Review I2: a Spring Boot root project owned every subproject's
    # endpoints. The deepest owning project wins, like Maven's nearest POM.
    _gradle_shop(tmp_path, client_main=True)

    owners = dict(_index(tmp_path)["endpoints"])

    assert owners["src/main/java/com/ex/gw/GatewayApp.java"] == "shop"
    assert owners["orders/src/main/java/com/ex/orders/OrdersApp.java"] == "orders"
    assert owners["billing/src/main/java/com/ex/billing/BillingApp.java"] == "billing"
    assert owners["orders-client/src/main/java/com/ex/oc/TargetClient.java"] == "orders-client"


def test_gradle_nested_services_are_owned_by_the_inner_service(tmp_path: Path) -> None:
    # Review I2: for nested services the outer one won.
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'platform'\ninclude 'outer', 'outer:inner'\n", encoding="utf-8",
    )
    (tmp_path / "outer/inner").mkdir(parents=True)
    (tmp_path / "outer/build.gradle").write_text("\n", encoding="utf-8")
    (tmp_path / "outer/inner/build.gradle").write_text("\n", encoding="utf-8")
    _app(tmp_path / "outer", "outer", "OuterApp")
    _app(tmp_path / "outer/inner", "inner", "InnerApp")

    owners = dict(_index(tmp_path)["endpoints"])

    assert owners["outer/inner/src/main/java/com/ex/inner/InnerApp.java"] == "inner"
    assert owners["outer/src/main/java/com/ex/outer/OuterApp.java"] == "outer"


def test_gradle_library_endpoints_name_their_module_and_keep_relations(tmp_path: Path) -> None:
    # Review I3: an endpoint in a Gradle library had no module, so its
    # relations were dropped; ownership depended on a main class elsewhere.
    _gradle_shop(tmp_path, client_main=False)

    facts = _index(tmp_path)
    owners = dict(facts["endpoints"])
    identities = {identity for _path, identity in facts["modules"]}

    assert owners["orders-client/src/main/java/com/ex/oc/TargetClient.java"] == "orders-client"
    assert set(owners.values()) <= identities, (owners, identities)
    assert ("orders-client", "calls", "GET /billing/items", "orders-client") in facts["relations"]


def test_module_dependencies_require_the_local_group_and_skip_test_scopes(
    tmp_path: Path,
) -> None:
    # Review I4 (probe p3): an external io.github.resilience4j:core became
    # an edge to the local module ``core``, and a test-scoped dependency
    # became a runtime edge.
    from systemlens.discovery.build.modules import discover_module_dependencies

    _aggregator(tmp_path / "pom.xml", "orders", "core", "fixtures", "shared")
    _child_pom(tmp_path / "core/pom.xml", "core")
    _child_pom(tmp_path / "fixtures/pom.xml", "fixtures")
    _child_pom(tmp_path / "shared/pom.xml", "shared", group="com.ex.shared")
    _child_pom(
        tmp_path / "orders/pom.xml",
        "orders",
        "<dependency><groupId>io.github.resilience4j</groupId><artifactId>core</artifactId>"
        "<version>1</version></dependency>"
        "<dependency><groupId>com.ex</groupId><artifactId>fixtures</artifactId>"
        "<scope>test</scope></dependency>"
        "<dependency><groupId>${project.groupId}.shared</groupId><artifactId>shared</artifactId>"
        "<scope>provided</scope></dependency>",
    )

    modules = discover_modules(tmp_path)
    edges = [(edge.source, edge.target) for edge in discover_module_dependencies(tmp_path, modules)]

    assert edges == [("orders", "shared")], edges


def test_gradle_test_configurations_give_no_module_dependency(tmp_path: Path) -> None:
    # Review I4 (probe p2): ``testImplementation project(':billing')`` became
    # a runtime module dependency. compileOnly and runtimeOnly stay edges.
    from systemlens.discovery.build.modules import discover_module_dependencies

    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'shop'\n"
        "include 'orders', 'billing', 'fixtures', 'api', 'driver', 'it-support'\n",
        encoding="utf-8",
    )
    for name in ("billing", "fixtures", "api", "driver", "it-support"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "build.gradle").write_text("\n", encoding="utf-8")
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders/build.gradle.kts").write_text(
        "dependencies {\n"
        '  testImplementation(project(":billing"))\n'
        '  testFixturesApi(project(":fixtures"))\n'
        '  androidTestImplementation(project(":billing"))\n'
        '  integrationTestImplementation(project(":it-support"))\n'
        '  compileOnly(project(":api"))\n'
        '  runtimeOnly(project(path = ":driver"))\n'
        "}\n",
        encoding="utf-8",
    )

    modules = discover_modules(tmp_path)
    edges = [(edge.source, edge.target) for edge in discover_module_dependencies(tmp_path, modules)]

    assert edges == [("orders", "api"), ("orders", "driver")], edges


def test_deep_modules_are_inventoried_and_own_their_endpoints(tmp_path: Path) -> None:
    # Review I7 (probe p10): modules deeper than five levels were dropped
    # from the inventory while their endpoints still named them.
    (tmp_path / "pom.xml").write_text(
        "<project><groupId>com.ex</groupId><artifactId>root</artifactId><version>1</version>"
        "<packaging>pom</packaging></project>",
        encoding="utf-8",
    )
    deep = tmp_path / "platform/domains/sales/services/orders/api"
    _child_pom(deep / "pom.xml", "orders-api")
    _app(deep, "api", "ApiApp")
    (tmp_path / "node_modules/lib").mkdir(parents=True)
    _child_pom(tmp_path / "node_modules/lib/pom.xml", "vendored")

    facts = _index(tmp_path)
    identities = {identity for _path, identity in facts["modules"]}

    assert "orders-api" in identities, facts["modules"]
    assert "vendored" not in identities
    assert {module for _path, module in facts["endpoints"]} == {"orders-api"}


def test_gradle_settings_declare_the_build(tmp_path: Path) -> None:
    # Review I9 (probe p12): buildSrc, convention builds and stray build
    # files became modules although settings.gradle does not include them.
    (tmp_path / "settings.gradle").write_text(
        "// include 'old:legacy-svc'\n"
        "pluginManagement { includeBuild('gradle/plugins') }\n"
        "rootProject.name = 'shop'\n"
        "include 'orders',\n        'billing'\n",
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text("\n", encoding="utf-8")
    for directory in ("orders", "billing", "buildSrc", "gradle/plugins/conventions", "old/legacy-svc"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "orders/build.gradle").write_text("\n", encoding="utf-8")
    (tmp_path / "billing/build.gradle").write_text("\n", encoding="utf-8")
    (tmp_path / "buildSrc/build.gradle.kts").write_text("plugins { `kotlin-dsl` }\n", encoding="utf-8")
    (tmp_path / "gradle/plugins/settings.gradle.kts").write_text("\n", encoding="utf-8")
    (tmp_path / "gradle/plugins/conventions/build.gradle.kts").write_text("\n", encoding="utf-8")
    (tmp_path / "old/legacy-svc/build.gradle").write_text("\n", encoding="utf-8")
    _app(tmp_path / "orders", "orders", "OrdersApp")
    _app(tmp_path / "old/legacy-svc", "legacy", "LegacyApp")

    facts = _index(tmp_path)

    assert sorted(path for path, _identity in facts["modules"]) == [".", "billing", "orders"]
    assert [path for path, _module in facts["endpoints"]] == [
        "orders/src/main/java/com/ex/orders/OrdersApp.java"
    ]


def test_gradle_settings_project_dir_remaps_are_followed(tmp_path: Path) -> None:
    # Review I9 (probe p9): a ``projectDir`` remap names the project after its
    # project path, and a project dependency reaches the remapped directory.
    from systemlens.discovery.build.modules import discover_module_dependencies

    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'shop'\ninclude 'orders', 'billing'\n"
        "project(':billing').projectDir = file('libs/billing-api')\n",
        encoding="utf-8",
    )
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders/build.gradle").write_text(
        "dependencies { implementation project(':billing') }\n", encoding="utf-8",
    )
    (tmp_path / "libs/billing-api").mkdir(parents=True)
    (tmp_path / "libs/billing-api/build.gradle").write_text("\n", encoding="utf-8")

    modules = discover_modules(tmp_path)
    edges = [(edge.source, edge.target) for edge in discover_module_dependencies(tmp_path, modules)]

    assert {(module.name, module.path.relative_to(tmp_path.resolve()).as_posix()) for module in modules} >= {
        ("billing", "libs/billing-api"), ("orders", "orders"),
    }
    assert edges == [("orders", "billing")], edges


def test_gradle_computed_includes_keep_every_build_file(tmp_path: Path) -> None:
    # A settings file that computes its includes declares nothing statically:
    # discovery keeps every build file, as without settings (ADR-80).
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'shop'\n"
        "file('services').eachDir { include \"services:${it.name}\" }\n",
        encoding="utf-8",
    )
    (tmp_path / "services/orders").mkdir(parents=True)
    (tmp_path / "services/orders/build.gradle").write_text("\n", encoding="utf-8")

    names = sorted(module.name for module in discover_modules(tmp_path))

    assert names == ["orders", "shop"], names


def test_maven_aggregator_declares_the_build(tmp_path: Path) -> None:
    # Review I9: Maven ``<modules>`` are followed recursively; integration
    # test projects under src/it and stray POMs are not modules, and their
    # sources are not indexed.
    _aggregator(tmp_path / "pom.xml", "services")
    services = tmp_path / "services/pom.xml"
    services.parent.mkdir(parents=True)
    services.write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        "<parent><groupId>com.ex</groupId><artifactId>root</artifactId><version>1.0</version></parent>"
        "<artifactId>services</artifactId><packaging>pom</packaging>"
        "<profiles><profile><modules><module>orders</module></modules></profile></profiles>"
        "</project>",
        encoding="utf-8",
    )
    _child_pom(tmp_path / "services/orders/pom.xml", "orders")
    _app(tmp_path / "services/orders", "orders", "OrdersApp")
    _child_pom(tmp_path / "services/orders/src/it/sample/pom.xml", "it-sample")
    _app(tmp_path / "services/orders/src/it/sample", "sample", "SampleApp")
    _child_pom(tmp_path / "old/legacy/pom.xml", "legacy")
    _app(tmp_path / "old/legacy", "legacy", "LegacyApp")

    facts = _index(tmp_path)

    assert [identity for _path, identity in facts["modules"]] == ["root", "services", "orders"]
    assert facts["endpoints"] == [
        ("services/orders/src/main/java/com/ex/orders/OrdersApp.java", "orders")
    ]


def test_module_identity_change_rescans_the_renamed_module(tmp_path: Path) -> None:
    # Review I6 (probe p1): a second module with the same artifactId renamed
    # the first module's identity, but its endpoints kept the old one until
    # ``--full``. The reverse holds when the duplicate disappears.
    import shutil

    # The root POM does not change, so no root-level input forces a full
    # rescan; only the new module's own files are new.
    _aggregator(tmp_path / "pom.xml", "a", "b")
    _child_pom(tmp_path / "a/pom.xml", "orders")
    _app(tmp_path / "a", "a", "AApp")
    first = _index(tmp_path)
    assert {module for _path, module in first["endpoints"]} == {"orders"}

    _child_pom(tmp_path / "b/pom.xml", "orders")
    _app(tmp_path / "b", "b", "BApp")
    second = _index(tmp_path, full=False)
    identities = {identity for _path, identity in second["modules"]}
    assert dict(second["endpoints"]) == {
        "a/src/main/java/com/ex/a/AApp.java": "orders@a",
        "b/src/main/java/com/ex/b/BApp.java": "orders@b",
    }
    for kind in ("flow_modules", "method_modules"):
        assert set(second[kind]) <= identities, (kind, second[kind])
    assert {row[3] for row in second["relations"] if row[3]} <= identities

    shutil.rmtree(tmp_path / "b")
    third = _index(tmp_path, full=False)
    assert dict(third["endpoints"]) == {"a/src/main/java/com/ex/a/AApp.java": "orders"}
