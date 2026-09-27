from pathlib import Path

from systemlens.indexing.file_inventory import list_repo_files
from systemlens.infrastructure.config import Config
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.indexing.file_inventory import dependent_rescan_paths


def test_inventory_excludes_maven_and_gradle_build_outputs(tmp_path: Path) -> None:
    (tmp_path / "service/src/main/java").mkdir(parents=True)
    (tmp_path / "service/target/classes").mkdir(parents=True)
    (tmp_path / "service/build/resources/main").mkdir(parents=True)
    (tmp_path / "service/src/main/java/App.java").write_text("class App {}")
    (tmp_path / "service/target/classes/application.yml").write_text("spring: {}")
    (tmp_path / "service/build/resources/main/application.yml").write_text("spring: {}")

    files = list_repo_files(tmp_path, Config())

    assert set(files) == {"service/src/main/java/App.java"}


def test_inventory_keeps_source_packages_named_build(tmp_path: Path) -> None:
    source = tmp_path / "service/src/main/java/com/example/build/Order.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Order {}", encoding="utf-8")

    files = list_repo_files(tmp_path, Config())

    assert set(files) == {"service/src/main/java/com/example/build/Order.java"}


def test_dependent_rescan_is_limited_to_the_changed_module(tmp_path: Path) -> None:
    orders = tmp_path / "orders"
    payments = tmp_path / "payments"
    modules = [
        DiscoveredModule("orders", orders, "maven", None, "microservice", True, ""),
        DiscoveredModule("payments", payments, "maven", None, "microservice", True, ""),
    ]
    current_paths = {
        "orders/pom.xml",
        "orders/src/main/java/Orders.java",
        "orders/src/main/resources/application.yml",
        "payments/pom.xml",
        "payments/src/main/java/Payments.java",
    }

    assert dependent_rescan_paths(
        {"orders/src/main/resources/application.yml"},
        current_paths,
        tmp_path,
        modules,
    ) == {
        "orders/pom.xml",
        "orders/src/main/java/Orders.java",
        "orders/src/main/resources/application.yml",
    }


def test_dependent_rescan_requires_global_refresh_for_root_configuration(tmp_path: Path) -> None:
    modules = [DiscoveredModule("orders", tmp_path / "orders", "maven", None, "microservice", True, "")]

    assert dependent_rescan_paths(
        {"application.yml"},
        {"application.yml", "orders/src/main/java/Orders.java"},
        tmp_path,
        modules,
    ) is None
