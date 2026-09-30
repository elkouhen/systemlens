from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

from systemlens.domain.code_flows import CodeFlow, CodeFlowStep, IntegrationMethod
from systemlens.domain.models import MessageEndpoint, compute_endpoint_id
from systemlens.render.debug_xlsx import write_debug_xlsx


def _endpoint(role: str, topic: str, line: int, message_type: str) -> MessageEndpoint:
    return MessageEndpoint(
        id=compute_endpoint_id(role, topic, "Orders.java", line),
        role=role,
        system="kafka" if role in {"consume", "produce"} else "rest",
        topic=topic,
        topic_dynamic=False,
        source="code",
        framework="spring",
        path="Orders.java",
        start_line=line,
        end_line=line,
        snippet="evidence",
        module="orders",
        message_type=message_type,
    )


def test_debug_xlsx_lists_ports_and_internal_flow(tmp_path: Path) -> None:
    input_endpoint = _endpoint("consume", "orders.created", 10, "OrderCreated")
    output_endpoint = _endpoint("produce", "orders.accepted", 20, "OrderAccepted")
    methods = [
        IntegrationMethod("in", "orders", "Orders.consume", "Orders.java", 10, 12, (input_endpoint.id,), ()),
        IntegrationMethod("out", "orders", "Orders.publish", "Orders.java", 20, 22, (), (output_endpoint.id,)),
    ]
    flow = CodeFlow(
        "flow", "orders", "Orders.consume", "Orders.java", 10, 12,
        "resolved", "high", "evidence",
        (CodeFlowStep(1, "message_entry", input_endpoint.topic, "Orders.java", 10, 10, input_endpoint.id),
         CodeFlowStep(2, "message_publish", output_endpoint.topic, "Orders.java", 20, 20, output_endpoint.id)),
    )
    destination = tmp_path / "debug.xlsx"

    write_debug_xlsx(destination, {"orders": [input_endpoint, output_endpoint]}, methods, [flow])

    with ZipFile(destination) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
        sheets = "\n".join(archive.read(name).decode() for name in archive.namelist() if name.startswith("xl/worksheets/"))
    assert "Synthèse" in workbook
    assert "Ports" in workbook
    assert "Flux internes" in workbook
    assert "Légende" in workbook
    assert "Statut" in sheets
    assert "Alertes" in sheets
    assert "Orders.consume" not in sheets
    assert "Orders.publish" not in sheets
    assert "OrderCreated" in sheets
    assert "OrderAccepted" in sheets
    assert 'width="58"' not in sheets
    assert 'width="45"' in sheets
    assert "orders.created" in sheets
    assert "orders.accepted" in sheets
    assert sheets.count("<row") == 13


def test_debug_xlsx_highlights_service_without_internal_flow(tmp_path: Path) -> None:
    endpoint = _endpoint("consume", "orders.created", 10, "OrderCreated")
    destination = tmp_path / "debug.xlsx"

    write_debug_xlsx(destination, {"orders": [endpoint]}, [], [])

    with ZipFile(destination) as archive:
        sheets = "\n".join(
            archive.read(name).decode()
            for name in archive.namelist()
            if name.startswith("xl/worksheets/")
        )
    assert "À vérifier" in sheets
    assert "Aucun flux interne" in sheets
    assert "Classe non résolue : IN KAFKA orders.created (Orders.java:10)" in sheets
    assert "Type non résolu : IN KAFKA orders.created (Orders.java:10)" not in sheets
    assert 's="3"' in sheets


def test_debug_xlsx_does_not_flag_missing_rest_payload_type(tmp_path: Path) -> None:
    endpoint = replace(
        _endpoint("consume", "GET /api/health", 10, "HealthStatus"),
        role="serve",
        system="rest",
        message_type=None,
    )
    destination = tmp_path / "debug.xlsx"

    write_debug_xlsx(destination, {"orders": [endpoint]}, [], [])

    with ZipFile(destination) as archive:
        sheets = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "Type non résolu" not in sheets
