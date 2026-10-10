from systemlens.application.flow_consistency import (
    diagnose_flow_consistency,
    diagnose_flow_consistency_snapshot,
)
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep


def _flow(*steps: CodeFlowStep) -> CodeFlow:
    return CodeFlow(
        id="flow-1",
        module="orders",
        method="Orders.handle",
        path="Orders.java",
        start_line=1,
        end_line=40,
        status="potential",
        confidence="medium",
        reason="fixture",
        steps=steps,
    )


def test_async_write_then_causal_sync_read_is_a_potential_risk() -> None:
    diagnostics = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "message_entry", "orders.created", "Orders.java", 1, 1),
        CodeFlowStep(
            2, "data_write", "orders", "Orders.java", 10, 10,
            branch="async", resource="orders", key="order-42",
            expected_version="v2", completion="ack_only",
        ),
        CodeFlowStep(
            3, "data_read", "orders", "Orders.java", 20, 20,
            branch="sync", resource="orders", key="order-42",
            expected_version="v2", query_shape="point",
        ),
    ))
    assert len(diagnostics) == 1
    assert diagnostics[0].classification == "potential_risk"
    assert diagnostics[0].severity == "P1"


def test_visibility_guarantee_is_not_reported_as_risk() -> None:
    diagnostics = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "message_entry", "orders.created", "Orders.java", 1, 1),
        CodeFlowStep(
            2, "data_write", "orders", "Orders.java", 10, 10,
            branch="async", resource="orders", key="order-42",
            expected_version="v2", completion="visible",
        ),
        CodeFlowStep(
            3, "data_read", "orders", "Orders.java", 20, 20,
            branch="sync", resource="orders", key="order-42",
            expected_version="v2", query_shape="point",
        ),
    ))
    assert diagnostics[0].classification == "guarantee_identified"
    assert diagnostics[0].severity == "info"


def test_unknown_key_overlap_is_insufficient_evidence() -> None:
    diagnostics = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "message_entry", "orders.created", "Orders.java", 1, 1),
        CodeFlowStep(2, "data_write", "orders", "Orders.java", 10, 10,
                     branch="async", resource="orders"),
        CodeFlowStep(3, "data_read", "orders", "Orders.java", 20, 20,
                     branch="sync", resource="orders", query_shape="list"),
    ))
    assert diagnostics[0].classification == "insufficient_evidence"
    assert diagnostics[0].confidence == "low"


def test_distinct_pairs_are_reported_independently() -> None:
    diagnostics = diagnose_flow_consistency_snapshot([
        _flow(
            CodeFlowStep(1, "message_entry", "orders.created", "Orders.java", 1, 1),
            CodeFlowStep(2, "data_write", "orders", "Orders.java", 10, 10,
                         branch="async", resource="orders", key="a"),
            CodeFlowStep(3, "data_write", "orders", "Orders.java", 11, 11,
                         branch="async", resource="orders", key="b"),
            CodeFlowStep(4, "data_read", "orders", "Orders.java", 20, 20,
                         branch="sync", resource="orders", key="a"),
            CodeFlowStep(5, "data_read", "orders", "Orders.java", 21, 21,
                         branch="sync", resource="orders", key="b"),
        )
    ])
    assert [(item.write_step, item.read_step) for item in diagnostics] == [(2, 4), (3, 5)]
