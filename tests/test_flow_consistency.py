import json
from dataclasses import replace
from pathlib import Path

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


def test_read_before_async_write_is_not_classified_as_stale_read_risk() -> None:
    diagnostic, = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1),
        CodeFlowStep(2, "data_read", "orders", "Orders.java", 2, 2,
                     branch="sync", resource="orders", key="order-42"),
        CodeFlowStep(3, "data_write", "orders", "Orders.java", 3, 3,
                     branch="async", resource="orders", key="order-42"),
    ))
    assert diagnostic.classification == "insufficient_evidence"
    assert "avant" in (diagnostic.limitation or "")


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


def test_accesses_in_distinct_handlers_need_a_shared_persisted_root() -> None:
    write = _flow(CodeFlowStep(
        1, "data_write", "orders", "Writer.java", 10, 10,
        endpoint_id="writer-in", branch="async", resource="orders", key="request.orderId",
        causal_id="request-id", completion="ack_only",
    ))
    read = _flow(CodeFlowStep(
        1, "data_read", "orders", "Reader.java", 20, 20,
        endpoint_id="reader-in", branch="sync", resource="orders", key="request.orderId",
        causal_id="request-id",
    ))
    write, read = replace(write, id="writer"), replace(read, id="reader")
    assert diagnose_flow_consistency_snapshot([write, read]) == []
    diagnostic, = diagnose_flow_consistency_snapshot(
        [write, read], {"checkout-root": {"writer", "reader"}},
        {"checkout-root": {
            "writer": ("checkout-out", "writer-in"),
            "reader": ("checkout-out", "shipping-event", "reader-in"),
        }},
    )
    assert diagnostic.flow_id == "checkout-root"
    assert diagnostic.write_flow_id == "writer"
    assert diagnostic.read_flow_id == "reader"
    assert diagnostic.write_endpoint_id == "writer-in"
    assert diagnostic.read_endpoint_id == "reader-in"
    assert diagnostic.write_path_endpoint_ids == ("checkout-out", "writer-in")
    assert diagnostic.read_path_endpoint_ids == ("checkout-out", "shipping-event", "reader-in")
    assert diagnostic.classification == "potential_risk"


def test_pair_is_reported_on_every_reachable_call_root() -> None:
    write = replace(_flow(CodeFlowStep(
        1, "data_write", "orders", "Writer.java", 10, 10,
        branch="async", resource="orders", key="order-1",
    )), id="writer")
    read = replace(_flow(CodeFlowStep(
        1, "data_read", "orders", "Reader.java", 20, 20,
        branch="sync", resource="orders", key="order-1",
    )), id="reader")
    diagnostics = diagnose_flow_consistency_snapshot(
        [write, read], {"root-a": {"writer", "reader"}, "root-b": {"writer", "reader"}},
    )
    assert [(item.flow_id, item.classification) for item in diagnostics] == [
        ("root-a", "potential_risk"), ("root-b", "potential_risk"),
    ]


def test_conflicting_causal_ids_on_reachable_branches_are_insufficient() -> None:
    write = replace(_flow(CodeFlowStep(
        1, "data_write", "orders", "Writer.java", 10, 10,
        branch="async", resource="orders", key="order-1", causal_id="request-a",
    )), id="writer")
    read = replace(_flow(CodeFlowStep(
        1, "data_read", "orders", "Reader.java", 20, 20,
        branch="sync", resource="orders", key="order-1", causal_id="request-b",
    )), id="reader")
    diagnostic, = diagnose_flow_consistency_snapshot(
        [write, read], {"checkout-root": {"writer", "reader"}},
    )
    assert diagnostic.classification == "insufficient_evidence"
    assert diagnostic.limitation == "identifiants causaux incompatibles"


def test_different_key_expressions_are_reported_as_unknown_overlap() -> None:
    diagnostics = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "http_entry", "PUT /orders", "Orders.java", 1, 1),
        CodeFlowStep(2, "data_write", "orders", "Writer.java", 10, 10,
                     branch="async", resource="orders", key="request.orderId"),
        CodeFlowStep(3, "data_read", "orders", "Reader.java", 20, 20,
                     branch="sync", resource="orders", key="request.otherOrderId"),
    ))
    assert len(diagnostics) == 1
    assert diagnostics[0].classification == "insufficient_evidence"
    assert diagnostics[0].key is None


def test_commit_without_matching_visible_version_is_not_a_guarantee() -> None:
    diagnostics = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "message_entry", "orders.updated", "Orders.java", 1, 1),
        CodeFlowStep(2, "data_write", "orders", "Writer.java", 10, 10,
                     branch="async", resource="orders", key="order-1",
                     expected_version="v2", completion="committed"),
        CodeFlowStep(3, "data_read", "orders", "Reader.java", 20, 20,
                     branch="sync", resource="orders", key="order-1"),
    ))
    assert diagnostics[0].classification == "potential_risk"


def test_unknown_branch_is_not_assumed_to_be_synchronous() -> None:
    diagnostics = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "message_entry", "orders.updated", "Orders.java", 1, 1),
        CodeFlowStep(2, "data_write", "orders", "Writer.java", 10, 10,
                     branch="async", resource="orders", key="order-1"),
        CodeFlowStep(3, "data_read", "orders", "Reader.java", 20, 20,
                     resource="orders", key="order-1"),
    ))
    assert diagnostics == []
def test_checked_in_fact_base_matches_expected_classifications() -> None:
    manifest = json.loads(
        (Path(__file__).parents[1] / "examples" / "async-write-sync-read-facts.json")
        .read_text(encoding="utf-8")
    )
    flows = []
    for raw in manifest["flows"]:
        flows.append(CodeFlow(
            id=raw["id"], module=raw["module"], method=raw["method"], path=raw["path"],
            start_line=raw["start_line"], end_line=raw["end_line"], status=raw["status"],
            confidence=raw["confidence"], reason=raw["reason"],
            steps=tuple(CodeFlowStep(**step) for step in raw["steps"]),
        ))
    actual = {
        flow.id: [item.classification for item in diagnose_flow_consistency(flow)]
        for flow in flows
    }
    expected = {raw["id"]: raw["expected_diagnostics"] for raw in manifest["flows"]}
    assert actual == expected
