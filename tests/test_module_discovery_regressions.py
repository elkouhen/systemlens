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
