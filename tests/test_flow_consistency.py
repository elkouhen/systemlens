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
            branch="async", resource="orders", resource_identity="db:orders",
            key="order-42", key_identity="db:orders:42",
            expected_version="v2", completion="ack_only", causal_id="request-1",
            request_before_reads=("flow-1:3",),
        ),
        CodeFlowStep(
            3, "data_read", "orders", "Orders.java", 20, 20,
            branch="sync", resource="orders", resource_identity="db:orders",
            key="order-42", key_identity="db:orders:42",
            expected_version="v2", query_shape="point", causal_id="request-1",
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


def test_matching_visible_version_without_read_order_is_insufficient() -> None:
    diagnostics = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "message_entry", "orders.created", "Orders.java", 1, 1),
        CodeFlowStep(
            2, "data_write", "orders", "Orders.java", 10, 10,
            branch="async", resource="orders", resource_identity="db:orders",
            key="order-42", key_identity="db:orders:42",
            expected_version="v2", completion="visible", causal_id="request-1",
        ),
        CodeFlowStep(
            3, "data_read", "orders", "Orders.java", 20, 20,
            branch="sync", resource="orders", resource_identity="db:orders",
            key="order-42", key_identity="db:orders:42",
            expected_version="v2", query_shape="point", causal_id="request-1",
        ),
    ))
    assert diagnostics[0].classification == "insufficient_evidence"
    assert diagnostics[0].limitation == "visibilité antérieure au début de la lecture non prouvée"


def test_explicit_visibility_order_for_matching_version_identifies_guarantee() -> None:
    flow = _flow(
        CodeFlowStep(1, "message_entry", "orders.created", "Orders.java", 1, 1),
        CodeFlowStep(
            2, "data_write", "orders", "Orders.java", 10, 10,
            branch="async", resource="orders", resource_identity="db:orders",
            key="order-42", key_identity="db:orders:42",
            expected_version="v2", completion="visible",
            visibility_before_reads=("flow-1:3",), causal_id="request-1",
        ),
        CodeFlowStep(
            3, "data_read", "orders", "Orders.java", 20, 20,
            branch="sync", resource="orders", resource_identity="db:orders",
            key="order-42", key_identity="db:orders:42",
            expected_version="v2", query_shape="point", causal_id="request-1",
        ),
    )

    diagnostic, = diagnose_flow_consistency(flow)

    assert diagnostic.classification == "guarantee_identified"
    assert diagnostic.severity == "info"


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
                         branch="async", resource="orders", resource_identity="db:orders",
                         key="a", key_identity="db:orders:a", causal_id="request-1",
                         request_before_reads=("flow-1:4",)),
            CodeFlowStep(3, "data_write", "orders", "Orders.java", 11, 11,
                         branch="async", resource="orders", resource_identity="db:orders",
                         key="b", key_identity="db:orders:b", causal_id="request-1",
                         request_before_reads=("flow-1:5",)),
            CodeFlowStep(4, "data_read", "orders", "Orders.java", 20, 20,
                         branch="sync", resource="orders", resource_identity="db:orders",
                         key="a", key_identity="db:orders:a", causal_id="request-1"),
            CodeFlowStep(5, "data_read", "orders", "Orders.java", 21, 21,
                         branch="sync", resource="orders", resource_identity="db:orders",
                         key="b", key_identity="db:orders:b", causal_id="request-1"),
        )
    ])
    assert [(item.write_step, item.read_step) for item in diagnostics] == [(2, 4), (3, 5)]


def test_resource_index_keeps_unknown_resources_and_excludes_unrelated_pairs() -> None:
    diagnostics = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1),
        CodeFlowStep(2, "data_write", "Orders", "Orders.java", 2, 2,
                     branch="async", resource="Orders", resource_identity="db:orders",
                     key="id", key_identity="db:orders:id", causal_id="request-1",
                     request_before_reads=("flow-1:4",)),
        CodeFlowStep(3, "data_read", "products", "Orders.java", 3, 3,
                     branch="sync", resource="products", key="id"),
        CodeFlowStep(4, "data_read", "orders", "Orders.java", 4, 4,
                     branch="sync", resource="orders", resource_identity="db:orders",
                     key="id", key_identity="db:orders:id", causal_id="request-1"),
        CodeFlowStep(5, "data_read", "", "Orders.java", 5, 5,
                     branch="sync", key="id"),
    ))
    assert [(item.read_step, item.classification) for item in diagnostics] == [
        (4, "potential_risk"), (5, "insufficient_evidence"),
    ]


def test_accesses_in_distinct_handlers_need_a_shared_persisted_root() -> None:
    write = _flow(CodeFlowStep(
        1, "data_write", "orders", "Writer.java", 10, 10,
        endpoint_id="writer-in", branch="async", resource="orders", resource_identity="db:orders",
        key="request.orderId", key_identity="db:orders:order-id",
        causal_id="request-id", completion="ack_only",
        request_before_reads=("reader:1",),
    ))
    read = _flow(CodeFlowStep(
        1, "data_read", "orders", "Reader.java", 20, 20,
        endpoint_id="reader-in", branch="sync", resource="orders", resource_identity="db:orders",
        key="request.orderId", key_identity="db:orders:order-id",
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


def test_exact_pair_does_not_hide_uncertain_pairs_in_shared_roots() -> None:
    write = replace(_flow(CodeFlowStep(
        1, "data_write", "orders", "Writer.java", 10, 10,
        branch="async", resource="orders", resource_identity="db:orders",
        key="request.orderId", key_identity="db:orders:order-id", causal_id="request-1",
        request_before_reads=("exact-reader:1", "uncertain-reader:1"),
    )), id="writer")
    exact_read = replace(_flow(CodeFlowStep(
        1, "data_read", "orders", "Reader.java", 20, 20,
        branch="sync", resource="orders", resource_identity="db:orders",
        key="request.orderId", key_identity="db:orders:order-id", causal_id="request-1",
    )), id="exact-reader")
    uncertain_read = replace(_flow(CodeFlowStep(
        1, "data_read", "orders", "OtherReader.java", 30, 30,
        branch="sync", resource="orders", key="message.orderId", causal_id="request-1",
    )), id="uncertain-reader")

    diagnostics = diagnose_flow_consistency_snapshot(
        [write, exact_read, uncertain_read],
        {
            "exact-root": {"writer", "exact-reader", "uncertain-reader"},
            "uncertain-root": {"writer", "uncertain-reader"},
        },
    )

    assert [(item.flow_id, item.classification) for item in diagnostics] == [
        ("exact-root", "potential_risk"),
        ("exact-root", "insufficient_evidence"),
        ("uncertain-root", "insufficient_evidence"),
    ]


def test_pair_is_reported_on_every_reachable_call_root() -> None:
    write = replace(_flow(CodeFlowStep(
        1, "data_write", "orders", "Writer.java", 10, 10,
        branch="async", resource="orders", resource_identity="db:orders",
        key="order-1", key_identity="db:orders:1",
        causal_id="request-1", request_before_reads=("reader:1",),
    )), id="writer")
    read = replace(_flow(CodeFlowStep(
        1, "data_read", "orders", "Reader.java", 20, 20,
        branch="sync", resource="orders", resource_identity="db:orders",
        key="order-1", key_identity="db:orders:1",
        causal_id="request-1",
    )), id="reader")
    diagnostics = diagnose_flow_consistency_snapshot(
        [write, read], {"root-a": {"writer", "reader"}, "root-b": {"writer", "reader"}},
    )
    assert [(item.flow_id, item.classification) for item in diagnostics] == [
        ("root-a", "potential_risk"), ("root-b", "potential_risk"),
    ]


def test_pair_inside_descendant_flow_is_reported_on_each_call_root() -> None:
    descendant = replace(_flow(
        CodeFlowStep(1, "message_entry", "orders.created", "Handler.java", 1, 1),
        CodeFlowStep(
            2, "data_write", "orders", "Handler.java", 2, 2,
            branch="async", resource="orders", resource_identity="db:orders",
            key="order.id", key_identity="db:orders:id", causal_id="request-1",
            request_before_reads=("handler:3",),
        ),
        CodeFlowStep(
            3, "data_read", "orders", "Handler.java", 3, 3,
            branch="sync", resource="orders", resource_identity="db:orders",
            key="order.id", key_identity="db:orders:id", causal_id="request-1",
        ),
    ), id="handler")

    diagnostics = diagnose_flow_consistency_snapshot(
        [descendant],
        {"root-a": {"handler"}, "root-b": {"handler"}},
        {"root-a": {"handler": ("a-out", "handler-in")},
         "root-b": {"handler": ("b-out", "handler-in")}},
    )

    assert [(item.flow_id, item.classification) for item in diagnostics] == [
        ("root-a", "potential_risk"), ("root-b", "potential_risk"),
    ]
    assert diagnostics[0].write_path_endpoint_ids == ("a-out", "handler-in")
    assert diagnostics[0].read_path_endpoint_ids == ("a-out", "handler-in")


def test_concurrent_writes_require_matching_canonical_resource_and_key() -> None:
    def writer(flow_id: str, resource_identity: str, key_identity: str) -> CodeFlow:
        return replace(_flow(CodeFlowStep(
            1, "data_write", "orders", "Writer.java", 10, 10,
            resource="orders", resource_identity=resource_identity,
            key="id", key_identity=key_identity, concurrency="parallel",
        )), id=flow_id)

    left = writer("writer-a", "db-a:orders", "db-a:orders:id")
    different_store = writer("writer-b", "db-b:orders", "db-b:orders:id")
    different_key = writer("writer-b", "db-a:orders", "db-a:orders:other-id")
    same_record = writer("writer-b", "db-a:orders", "db-a:orders:id")
    roots = {"root": {"writer-a", "writer-b"}}

    assert diagnose_flow_consistency_snapshot([left, different_store], roots) == []
    assert diagnose_flow_consistency_snapshot([left, different_key], roots) == []
    diagnostics = diagnose_flow_consistency_snapshot([left, same_record], roots)
    assert [(item.flow_id, item.classification) for item in diagnostics] == [
        ("root", "potential_concurrent_write"),
    ]


def test_distinct_causal_requests_can_have_concurrent_writes() -> None:
    def writer(flow_id: str, causal_id: str) -> CodeFlow:
        return replace(_flow(CodeFlowStep(
            1, "data_write", "orders", "Writer.java", 10, 10,
            resource="orders", resource_identity="db:orders",
            key="id", key_identity="db:orders:id",
            transaction_id=f"tx-{flow_id}", causal_id=causal_id,
            concurrency="independent_handlers",
        )), id=flow_id)

    diagnostics = diagnose_flow_consistency_snapshot(
        [writer("writer-a", "request-a"), writer("writer-b", "request-b")],
        {"root": {"writer-a", "writer-b"}},
    )
    assert [(item.flow_id, item.classification) for item in diagnostics] == [
        ("root", "potential_concurrent_write"),
    ]


def test_parallel_writes_in_one_flow_are_attributed_to_reachable_roots() -> None:
    flow = replace(_flow(
        CodeFlowStep(1, "data_write", "orders", "Writer.java", 10, 10,
                     resource="orders", resource_identity="db:orders",
                     key="id", key_identity="db:orders:id",
                     transaction_id="tx-a", concurrency="parallel"),
        CodeFlowStep(2, "data_write", "orders", "Writer.java", 11, 11,
                     resource="orders", resource_identity="db:orders",
                     key="id", key_identity="db:orders:id",
                     transaction_id="tx-b", concurrency="parallel"),
    ), id="handler")

    diagnostics = diagnose_flow_consistency_snapshot(
        [flow], {"root-a": {"handler"}, "root-b": {"handler"}},
    )
    assert [(item.flow_id, item.classification) for item in diagnostics] == [
        ("root-a", "potential_concurrent_write"),
        ("root-b", "potential_concurrent_write"),
    ]


def test_matching_conflict_guard_protects_concurrent_writes() -> None:
    def writer(flow_id: str, guard_scope: str) -> CodeFlow:
        return replace(_flow(CodeFlowStep(
            1, "data_write", "orders", "Writer.java", 10, 10,
            resource="orders", resource_identity="db:orders",
            key="id", key_identity="db:orders:id", concurrency="parallel",
            guard="version", guard_scope=guard_scope,
        )), id=flow_id)

    left = writer("left", "orders:id")
    protected = writer("right", "orders:id")
    unprotected = writer("right", "orders:other-id")
    roots = {"root": {"left", "right"}}
    protected_diagnostic, = diagnose_flow_consistency_snapshot(
        [left, protected], roots,
    )
    risk_diagnostic, = diagnose_flow_consistency_snapshot(
        [left, unprotected], roots,
    )
    assert protected_diagnostic.classification == "conflict_protection_identified"
    assert risk_diagnostic.classification == "potential_concurrent_write"


def test_read_write_anomalies_require_distinct_transactions() -> None:
    read = CodeFlowStep(1, "data_read", "orders", "Reader.java", 10, 10,
                        resource="orders", resource_identity="db:orders",
                        key="id", key_identity="db:orders:id",
                        transaction_id="tx-read", concurrency="parallel",
                        isolation="read_uncommitted")
    write = CodeFlowStep(2, "data_write", "orders", "Writer.java", 11, 11,
                         resource="orders", resource_identity="db:orders",
                         key="id", key_identity="db:orders:id",
                         transaction_id="tx-write", phase="uncommitted")

    diagnostic, = diagnose_flow_consistency(_flow(read, write))
    assert diagnostic.classification == "potential_dirty_read"
    uncertain, = diagnose_flow_consistency(_flow(
        replace(read, transaction_id=None), write,
    ))
    assert uncertain.classification == "potential_interaction"
    assert diagnose_flow_consistency(_flow(
        replace(read, transaction_id="tx-write"), write,
    )) == []


def test_freshness_requirement_refines_concurrent_read_write_interaction() -> None:
    diagnostic, = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "data_read", "orders", "Reader.java", 10, 10,
                     resource="orders", resource_identity="db:orders",
                     key="id", key_identity="db:orders:id",
                     concurrency="parallel", freshness_required=True),
        CodeFlowStep(2, "data_write", "orders", "Writer.java", 11, 11,
                     resource="orders", resource_identity="db:orders",
                     key="id", key_identity="db:orders:id"),
    ))
    assert diagnostic.classification == "potential_read_write_anomaly"


def test_lost_update_requires_two_value_dependent_transactions() -> None:
    def rmw(flow_id: str, transaction_id: str, causal_id: str) -> CodeFlow:
        return replace(_flow(
            CodeFlowStep(1, "data_read", "orders", "Handler.java", 10, 10,
                         resource="orders", resource_identity="db:orders",
                         key="id", key_identity="db:orders:id",
                         transaction_id=transaction_id, causal_id=causal_id,
                         fields=("amount",)),
            CodeFlowStep(2, "data_write", "orders", "Handler.java", 11, 11,
                         resource="orders", resource_identity="db:orders",
                         key="id", key_identity="db:orders:id",
                         transaction_id=transaction_id, causal_id=causal_id,
                         fields=("amount",), concurrency="independent_handlers",
                         value_dependencies=(f"{flow_id}:1",)),
        ), id=flow_id)

    left = rmw("left", "tx-left", "request-left")
    right = rmw("right", "tx-right", "request-right")
    roots = {"root": {"left", "right"}}
    diagnostics = diagnose_flow_consistency_snapshot([left, right], roots)
    assert [item.classification for item in diagnostics] == ["potential_lost_update"]
    assert diagnostics[0].write_dependency_step == 1
    assert diagnostics[0].read_dependency_step == 1

    no_dependency = replace(right, steps=(right.steps[0], replace(
        right.steps[1], value_dependencies=(),
    )))
    assert all(
        item.classification != "potential_lost_update"
        for item in diagnose_flow_consistency_snapshot([left, no_dependency], roots)
    )


def test_lost_update_can_compare_parallel_transactions_in_one_flow() -> None:
    steps = []
    for order, transaction_id in ((1, "tx-a"), (3, "tx-b")):
        steps.extend((
            CodeFlowStep(order, "data_read", "orders", "Handler.java", order, order,
                         resource="orders", resource_identity="db:orders",
                         key="id", key_identity="db:orders:id",
                         transaction_id=transaction_id, fields=("amount",)),
            CodeFlowStep(order + 1, "data_write", "orders", "Handler.java",
                         order + 1, order + 1,
                         resource="orders", resource_identity="db:orders",
                         key="id", key_identity="db:orders:id",
                         transaction_id=transaction_id, fields=("amount",),
                         concurrency="parallel",
                         value_dependencies=(f"flow-1:{order}",)),
        ))

    diagnostics = diagnose_flow_consistency(_flow(*steps))
    assert [item.classification for item in diagnostics] == ["potential_lost_update"]


def test_conflicting_causal_ids_on_reachable_branches_are_insufficient() -> None:
    write = replace(_flow(CodeFlowStep(
        1, "data_write", "orders", "Writer.java", 10, 10,
        branch="async", resource="orders", resource_identity="db:orders",
        key="order-1", key_identity="db:orders:1", causal_id="request-a",
    )), id="writer")
    read = replace(_flow(CodeFlowStep(
        1, "data_read", "orders", "Reader.java", 20, 20,
        branch="sync", resource="orders", resource_identity="db:orders",
        key="order-1", key_identity="db:orders:1", causal_id="request-b",
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


def test_matching_source_text_without_canonical_identity_is_insufficient() -> None:
    diagnostic, = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1),
        CodeFlowStep(2, "data_write", "orders", "Orders.java", 10, 10,
                     branch="async", resource="orders", resource_identity="db-a:orders",
                     key="id", causal_id="request-1"),
        CodeFlowStep(3, "data_read", "orders", "Orders.java", 20, 20,
                     branch="sync", resource="orders", resource_identity="db-a:orders",
                     key="id", causal_id="request-1"),
    ))

    assert diagnostic.classification == "insufficient_evidence"
    assert diagnostic.key_identity is None


def test_same_resource_and_key_labels_in_distinct_scopes_do_not_pair() -> None:
    assert diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1),
        CodeFlowStep(2, "data_write", "orders", "Writer.java", 10, 10,
                     branch="async", resource="orders", resource_identity="db-a:orders",
                     key="id", key_identity="db-a:orders:id"),
        CodeFlowStep(3, "data_read", "orders", "Reader.java", 20, 20,
                     branch="sync", resource="orders", resource_identity="db-b:orders",
                     key="id", key_identity="db-b:orders:id"),
    )) == []


def test_commit_without_matching_visible_version_is_not_a_guarantee() -> None:
    diagnostics = diagnose_flow_consistency(_flow(
        CodeFlowStep(1, "message_entry", "orders.updated", "Orders.java", 1, 1),
        CodeFlowStep(2, "data_write", "orders", "Writer.java", 10, 10,
                     branch="async", resource="orders", resource_identity="db:orders",
                     key="order-1", key_identity="db:orders:1",
                     expected_version="v2", completion="committed", causal_id="request-1",
                     request_before_reads=("flow-1:3",)),
        CodeFlowStep(3, "data_read", "orders", "Reader.java", 20, 20,
                     branch="sync", resource="orders", resource_identity="db:orders",
                     key="order-1", key_identity="db:orders:1", causal_id="request-1"),
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


_SYSTEMS = {
    "post-in": "rest", "kafka-out": "kafka", "consumer-in": "kafka",
    "rest-out": "rest", "stock-in": "rest", "relay-out": "kafka", "relay-in": "kafka",
}
_ORDER = "jpa:orders:Order"


def _graph_flow(flow_id: str, *steps: CodeFlowStep) -> CodeFlow:
    return replace(_flow(*steps), id=flow_id)


def _consumer(key_identity: str | None = None) -> CodeFlow:
    return _graph_flow(
        "consumer",
        CodeFlowStep(1, "message_entry", "orders", "Consumer.java", 5, 5, endpoint_id="consumer-in"),
        CodeFlowStep(2, "data_write", "Order", "Consumer.java", 9, 9, operation="save",
                     resource=_ORDER, resource_identity=_ORDER, key="event.id()",
                     key_identity=key_identity, branch="async"),
    )


def _read(order: int, line: int, key_identity: str | None = None) -> CodeFlowStep:
    return CodeFlowStep(order, "data_read", "Order", "Orders.java", line, line,
                        operation="findById", resource=_ORDER, resource_identity=_ORDER,
                        key="id", key_identity=key_identity, branch="sync")


def _publish(order: int, line: int) -> CodeFlowStep:
    return CodeFlowStep(order, "message_publish", "orders", "Orders.java", line, line,
                        endpoint_id="kafka-out")


_TO_CONSUMER = ("kafka-out", "consumer-in")


def test_call_graph_read_after_publish_is_a_potential_risk() -> None:
    root = _graph_flow(
        "root",
        CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1, endpoint_id="post-in"),
        _publish(2, 10), _read(3, 20),
    )
    flows = [root, _consumer()]
    paths = {"root": {"root": (), "consumer": _TO_CONSUMER}}

    diagnostic, = diagnose_flow_consistency_snapshot(
        flows, {"root": set(paths["root"])}, paths, _SYSTEMS,
    )

    assert diagnostic.flow_id == "root"
    assert diagnostic.classification == "potential_risk"
    assert diagnostic.confidence == "low"
    assert "enregistrement commun non prouvé" in (diagnostic.limitation or "")
    assert "Orders.java:10 avant Orders.java:20" in (diagnostic.order_evidence or "")
    assert diagnostic.write_path_endpoint_ids == _TO_CONSUMER


def test_call_graph_read_before_publish_is_not_reported() -> None:
    root = _graph_flow(
        "root",
        CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1, endpoint_id="post-in"),
        _read(2, 10), _publish(3, 20),
    )
    paths = {"root": {"root": (), "consumer": _TO_CONSUMER}}

    assert diagnose_flow_consistency_snapshot(
        [root, _consumer()], {"root": set(paths["root"])}, paths, _SYSTEMS,
    ) == []


def test_call_graph_orders_sibling_flows_at_their_divergent_call_sites() -> None:
    trigger = CodeFlowStep(1, "http_entry", "POST /orders", "Ctrl.java", 1, 1, endpoint_id="post-in")
    call = CodeFlowStep(2, "method_call", "OrderService.create", "Ctrl.java", 12, 12)
    publisher = _graph_flow("root-publish", trigger, call, replace(_publish(3, 30), path="Service.java"))
    reader = _graph_flow("root-read", trigger, call, replace(_read(3, 40), path="Service.java"))
    paths = {"root-publish": {"root-publish": (), "root-read": (), "consumer": _TO_CONSUMER}}

    diagnostic, = diagnose_flow_consistency_snapshot(
        [publisher, reader, _consumer()], {"root-publish": set(paths["root-publish"])},
        paths, _SYSTEMS,
    )

    assert diagnostic.classification == "potential_risk"
    assert "Service.java:30 avant Service.java:40" in (diagnostic.order_evidence or "")


def test_call_graph_rest_read_branch_with_matching_key_has_medium_confidence() -> None:
    root = _graph_flow(
        "root",
        CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1, endpoint_id="post-in"),
        _publish(2, 10),
        CodeFlowStep(3, "http_call", "GET /stock", "Orders.java", 20, 20, endpoint_id="rest-out"),
    )
    stock = _graph_flow(
        "stock",
        CodeFlowStep(1, "http_entry", "GET /stock", "Stock.java", 1, 1, endpoint_id="stock-in"),
        _read(2, 5, key_identity=f"{_ORDER}:id"),
    )
    paths = {"root": {"root": (), "consumer": _TO_CONSUMER, "stock": ("rest-out", "stock-in")}}

    diagnostic, = diagnose_flow_consistency_snapshot(
        [root, stock, _consumer(f"{_ORDER}:id")], {"root": set(paths["root"])}, paths, _SYSTEMS,
    )

    assert diagnostic.read_flow_id == "stock"
    assert (diagnostic.classification, diagnostic.confidence, diagnostic.severity) == (
        "potential_risk", "medium", "P1",
    )


def test_call_graph_skips_distinct_keys_and_asynchronous_read_branches() -> None:
    root = _graph_flow(
        "root",
        CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1, endpoint_id="post-in"),
        _publish(2, 10),
        CodeFlowStep(3, "message_publish", "relay", "Orders.java", 20, 20, endpoint_id="relay-out"),
    )
    relay = _graph_flow(
        "relay",
        CodeFlowStep(1, "message_entry", "relay", "Relay.java", 1, 1, endpoint_id="relay-in"),
        replace(_read(2, 5), branch=None),
    )
    distinct = _graph_flow(
        "root-distinct",
        CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1, endpoint_id="post-in"),
        _publish(2, 10), _read(3, 20, key_identity=f"{_ORDER}:other"),
    )
    paths = {
        "root": {"root": (), "consumer": _TO_CONSUMER, "relay": ("relay-out", "relay-in")},
        "root-distinct": {"root-distinct": (), "consumer": _TO_CONSUMER},
    }
    flows = [root, relay, distinct, _consumer(f"{_ORDER}:id")]

    assert diagnose_flow_consistency_snapshot(
        flows, {root_id: set(items) for root_id, items in paths.items()}, paths, _SYSTEMS,
    ) == []
