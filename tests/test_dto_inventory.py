from pathlib import Path

from systemlens.indexing.dto_inventory import materialize_kafka_dto_definitions
from systemlens.domain.models import MessageEndpoint
from systemlens.modules import DiscoveredModule


def _module(path: Path, name: str = "orders") -> DiscoveredModule:
    return DiscoveredModule(
        name=name,
        path=path,
        build_system="maven",
        version=None,
        kind="library",
        starts_application=True,
        configuration_example="",
    )


def _endpoint(message_type: str) -> MessageEndpoint:
    return MessageEndpoint(
        id=f"produce:{message_type}",
        role="produce",
        system="kafka",
        topic="orders.created",
        topic_dynamic=False,
        source="code",
        framework="spring-kafka",
        path="src/main/java/com/example/Publisher.java",
        start_line=4,
        end_line=4,
        snippet="",
        module="orders",
        qualified_name="com.example.Publisher",
        message_type=message_type,
    )


def _write_java(module_path: Path, package: str, name: str, declaration: str) -> None:
    directory = module_path / "src" / "main" / "java" / Path(*package.split("."))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.java").write_text(
        f"package {package}; {declaration}", encoding="utf-8"
    )


def test_materialize_kafka_dtos_follows_conservative_nested_references(
    tmp_path: Path,
) -> None:
    _write_java(
        tmp_path,
        "com.example",
        "OrderCreated",
        "public record OrderCreated(String id, Address address, Status status) {}",
    )
    _write_java(
        tmp_path,
        "com.example",
        "Address",
        "public record Address(String city) {}",
    )
    _write_java(
        tmp_path,
        "com.example",
        "Status",
        "public enum Status { CREATED, CANCELLED }",
    )

    definitions = materialize_kafka_dto_definitions(
        {"orders": [_endpoint("com.example.OrderCreated")]}, [_module(tmp_path)]
    )
    by_name = {definition["name"]: definition for definition in definitions}

    assert set(by_name) == {"Address", "OrderCreated", "Status"}
    assert by_name["OrderCreated"]["root"] is True
    assert by_name["Address"]["root"] is False
    assert by_name["Status"]["enum_values"] == ["CREATED", "CANCELLED"]
    assert by_name["OrderCreated"]["producers"] == ["orders"]
    assert by_name["OrderCreated"]["topics"] == ["orders.created"]
    assert by_name["OrderCreated"]["source"] == (
        "src/main/java/com/example/OrderCreated.java"
    )


def test_materialize_kafka_dtos_keeps_ambiguous_simple_names_unresolved(
    tmp_path: Path,
) -> None:
    _write_java(tmp_path, "com.first", "Event", "public record Event(String id) {}")
    _write_java(tmp_path, "com.second", "Event", "public record Event(String id) {}")

    definitions = materialize_kafka_dto_definitions(
        {"orders": [_endpoint("Event")]}, [_module(tmp_path)]
    )

    assert definitions == [
        {
            "id": "unresolved:Event",
            "name": "Event",
            "qualified_name": None,
            "fields": [],
            "source": None,
            "module": None,
            "producers": ["orders"],
            "consumers": [],
            "topics": ["orders.created"],
            "root": True,
        }
    ]
