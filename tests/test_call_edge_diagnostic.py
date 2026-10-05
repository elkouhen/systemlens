import json
from pathlib import Path

from typer.testing import CliRunner

from systemlens.delivery.cli import app
from systemlens.domain.code_flows import (
    CodeFlow,
    CodeFlowStep,
    CodeQLCallGraphEdge,
    IntegrationMethod,
)
from systemlens.storage.sqlite import Store


RUNNER = CliRunner()
POSSIBLE_EDGE_SIGNATURE = (
    "code-flow|engine=codeql|available=True|hops=12|edge-confidence=possible"
)


def _method(
    method_id: str,
    name: str,
    *,
    inputs: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
) -> IntegrationMethod:
    return IntegrationMethod(
        method_id,
        "orders",
        name,
        f"src/{name.rsplit('.', 2)[-2]}.java",
        10,
        20,
        inputs,
        outputs,
    )


def _given_persisted_snapshot(
    root: Path,
    methods: list[IntegrationMethod],
    *,
    edges: list[CodeQLCallGraphEdge] | None = None,
    flows: list[CodeFlow] | None = None,
    signature: str = POSSIBLE_EDGE_SIGNATURE,
    flow_status: str = "complete",
) -> None:
    with Store(root) as store:
        store.replace_integration_methods(methods)
        store.replace_codeql_call_edges(edges or [])
        store.replace_code_flows(flows or [])
        store.set_meta("code_flow_signature", signature)
        store.set_meta("codeql_call_graph_status", "complete")
        store.set_meta("code_flow_snapshot_status", flow_status)


def _invoke_json(
    root: Path, caller: str, callee: str, *, module: str | None = None,
) -> tuple[int, dict[str, object]]:
    arguments = ["analyze", "call-edge", caller, callee, "--root", str(root), "--json"]
    if module is not None:
        arguments.extend(["--module", module])
    result = RUNNER.invoke(
        app,
        arguments,
    )
    return result.exit_code, json.loads(result.output)


def test_call_edge_reports_the_missing_caller_from_the_persisted_snapshot(
    tmp_path: Path,
) -> None:
    # Given an indexed callee without the expected caller.
    callee = _method("callee", "orders.Service.reserve")
    _given_persisted_snapshot(tmp_path, [callee])

    # When the user diagnoses the expected call through the CLI.
    exit_code, payload = _invoke_json(
        tmp_path, "Controller.create", "Service.reserve",
    )

    # Then the command locates the gap in the method inventory.
    assert exit_code == 0
    assert payload["status"] == "caller_not_indexed"
    assert payload["stage"] == "method_inventory"
    assert payload["caller_candidates"] == []
    assert len(payload["callee_candidates"]) == 1
    assert payload["presence"] == {
        "caller_node": {"ast": "absent", "codeql": "unknown",
                         "proof_ast": "method_not_in_persisted_inventory",
                         "proof_codeql": "not_proven"},
        "callee_node": {"ast": "present", "codeql": "unknown",
                         "proof_ast": "persisted_integration_method",
                         "proof_codeql": "not_proven_by_persisted_non_inferred_edge"},
        "edge": {"ast": "unknown", "codeql": "unknown",
                 "proof_ast": "no_persisted_ast_call_edge_projection",
                 "proof_codeql": "no_matching_persisted_non_inferred_call_edge"},
    }
    assert "systemlens index --full" in str(payload["recommended_action"])


def test_call_edge_reports_a_codeql_or_join_gap_when_the_edge_is_missing(
    tmp_path: Path,
) -> None:
    # Given a complete snapshot containing both methods but no call edge.
    caller = _method("caller", "orders.Controller.create")
    callee = _method("callee", "orders.Service.reserve")
    _given_persisted_snapshot(tmp_path, [caller, callee])

    # When the user diagnoses the expected call through the CLI.
    exit_code, payload = _invoke_json(
        tmp_path, "Controller.create", "Service.reserve",
    )

    # Then the command locates the gap in CodeQL extraction or AST joining.
    assert exit_code == 0
    assert payload["status"] == "edge_not_persisted"
    assert payload["stage"] == "codeql_extraction_or_join"
    assert payload["edges"] == []
    assert payload["presence"] == {
        "caller_node": {"ast": "present", "codeql": "unknown",
                         "proof_ast": "persisted_integration_method",
                         "proof_codeql": "not_proven_by_persisted_non_inferred_edge"},
        "callee_node": {"ast": "present", "codeql": "unknown",
                         "proof_ast": "persisted_integration_method",
                         "proof_codeql": "not_proven_by_persisted_non_inferred_edge"},
        "edge": {"ast": "unknown", "codeql": "unknown",
                 "proof_ast": "no_persisted_ast_call_edge_projection",
                 "proof_codeql": "no_matching_persisted_non_inferred_call_edge"},
    }
    assert "--codeql-progress" in str(payload["recommended_action"])


def test_call_edge_does_not_blame_codeql_when_the_snapshot_is_partial(
    tmp_path: Path,
) -> None:
    # Given a partial snapshot containing both methods but no call edge.
    caller = _method("caller", "orders.Controller.create")
    callee = _method("callee", "orders.Service.reserve")
    _given_persisted_snapshot(
        tmp_path, [caller, callee], flow_status="partial",
    )

    # When the user diagnoses the expected call through the CLI.
    exit_code, payload = _invoke_json(
        tmp_path, "Controller.create", "Service.reserve",
    )

    # Then the command asks for a complete snapshot before assigning a cause.
    assert exit_code == 0
    assert payload["status"] == "snapshot_incomplete"
    assert payload["stage"] == "snapshot"
    assert payload["snapshot"]["flow_snapshot_status"] == "partial"


def test_call_edge_reports_a_possible_edge_filtered_by_exact_mode(
    tmp_path: Path,
) -> None:
    # Given a possible edge and a snapshot configured to accept exact edges only.
    caller = _method("caller", "orders.Controller.create", inputs=("in",))
    callee = _method("callee", "orders.Service.reserve", outputs=("out",))
    edge = CodeQLCallGraphEdge(
        "caller", "callee", "src/Controller.java", 15, "possible", inferred=True,
    )
    _given_persisted_snapshot(
        tmp_path,
        [caller, callee],
        edges=[edge],
        signature="code-flow|engine=codeql|available=True|hops=12|edge-confidence=exact",
    )

    # When the user diagnoses the expected call through the CLI.
    exit_code, payload = _invoke_json(
        tmp_path, "Controller.create", "Service.reserve",
    )

    # Then the command identifies the flow-reconstruction confidence filter.
    assert exit_code == 0
    assert payload["status"] == "edge_filtered_by_confidence"
    assert payload["stage"] == "flow_reconstruction"
    assert payload["edges"][0]["dispatch_confidence"] == "possible"
    assert payload["presence"] == {
        "caller_node": {"ast": "present", "codeql": "unknown",
                         "proof_ast": "persisted_integration_method",
                         "proof_codeql": "not_proven_by_persisted_non_inferred_edge"},
        "callee_node": {"ast": "present", "codeql": "unknown",
                         "proof_ast": "persisted_integration_method",
                         "proof_codeql": "not_proven_by_persisted_non_inferred_edge"},
        "edge": {"ast": "unknown", "codeql": "unknown",
                 "proof_ast": "no_persisted_ast_call_edge_projection",
                 "proof_codeql": "no_matching_persisted_non_inferred_call_edge"},
    }
    assert "--codeql-edge-confidence possible" in str(payload["recommended_action"])


def test_call_edge_matches_interface_query_to_concrete_dispatch_target(
    tmp_path: Path,
) -> None:
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders/OrderService.java").write_text(
        "package orders;\npublic interface OrderService {\n"
        "  void reserve(String value);\n}\n"
    )
    (tmp_path / "orders/OrderServiceImpl.java").write_text(
        "package orders;\npublic class OrderServiceImpl implements OrderService {\n"
        "  public void reserve(String value) {}\n}\n"
    )
    caller = _method("caller", "orders.OrderController.entry")
    interface_method = IntegrationMethod(
        "interface", "orders", "orders.OrderService.reserve",
        "orders/OrderService.java", 3, 3, (), (),
    )
    implementation = IntegrationMethod(
        "implementation", "orders", "orders.OrderServiceImpl.reserve",
        "orders/OrderServiceImpl.java", 3, 3, (), (),
    )
    edge = CodeQLCallGraphEdge(
        "caller", "implementation", "orders/OrderController.java", 12, "possible",
    )
    _given_persisted_snapshot(
        tmp_path, [caller, interface_method, implementation], edges=[edge],
    )

    exit_code, payload = _invoke_json(
        tmp_path, "OrderController.entry", "OrderService.reserve",
    )

    assert exit_code == 0
    assert payload["presence"]["callee_node"]["codeql"] == "present"
    assert payload["presence"]["edge"]["codeql"] == "present"
    assert payload["edges"] == [{
        "caller_id": "caller",
        "callee_id": "implementation",
        "path": "orders/OrderController.java",
        "line": 12,
        "dispatch_confidence": "possible",
        "inferred": False,
    }]


def test_call_edge_keeps_repository_wide_candidates_without_module_filter(
    tmp_path: Path,
) -> None:
    caller = _method("caller", "orders.Controller.create")
    orders_callee = _method("orders-callee", "orders.Service.reserve")
    billing_callee = IntegrationMethod(
        "billing-callee", "billing", "billing.Service.reserve",
        "billing/Service.java", 10, 20, (), (),
    )
    _given_persisted_snapshot(
        tmp_path, [caller, orders_callee, billing_callee],
    )

    exit_code, payload = _invoke_json(
        tmp_path, "Controller.create", "Service.reserve",
    )

    assert exit_code == 0
    assert payload["module"] is None
    assert payload["status"] == "method_selection_ambiguous"
    assert [item["id"] for item in payload["callee_candidates"]] == [
        "billing-callee", "orders-callee",
    ]


def test_call_edge_accepts_an_explicit_module_context(
    tmp_path: Path,
) -> None:
    orders_caller = _method("orders-caller", "orders.Controller.create")
    billing_caller = IntegrationMethod(
        "billing-caller", "billing", "billing.Controller.create",
        "billing/Controller.java", 10, 20, (), (),
    )
    billing_callee = IntegrationMethod(
        "billing-callee", "billing", "billing.Service.reserve",
        "billing/Service.java", 10, 20, (), (),
    )
    edge = CodeQLCallGraphEdge(
        "billing-caller", "billing-callee", "billing/Controller.java", 15, "exact",
    )
    _given_persisted_snapshot(
        tmp_path, [orders_caller, billing_caller, billing_callee], edges=[edge],
    )

    exit_code, payload = _invoke_json(
        tmp_path, "Controller.create", "Service.reserve", module="billing",
    )

    assert exit_code == 0
    assert payload["module"] == "billing"
    assert payload["status"] == "edge_outside_input_path"
    assert payload["caller_candidates"][0]["id"] == "billing-caller"


def test_call_edges_lists_persisted_calls_independently_of_flows(
    tmp_path: Path,
) -> None:
    caller = _method("caller", "orders.Controller.create")
    callee = IntegrationMethod(
        "callee", "billing", "billing.Service.reserve",
        "billing/Service.java", 10, 20, (), (),
    )
    edges = [
        CodeQLCallGraphEdge("caller", "callee", "orders/Controller.java", 15, "possible"),
        CodeQLCallGraphEdge("caller", "callee", "orders/Controller.java", 16, "possible", inferred=True),
    ]
    _given_persisted_snapshot(tmp_path, [caller, callee], edges=edges)

    result = RUNNER.invoke(
        app,
        ["analyze", "call-edges", "--module", "orders", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["edge_count"] == 2
    assert [edge["inferred"] for edge in payload["edges"]] == [False, True]
    assert payload["edges"][0]["callee"]["module"] == "billing"


def test_methods_lists_isolated_indexed_methods_by_module(tmp_path: Path) -> None:
    orders_method = _method("orders-method", "orders.Controller.create")
    billing_method = IntegrationMethod(
        "billing-method", "billing", "billing.Service.reserve",
        "billing/Service.java", 10, 20, (), (),
    )
    _given_persisted_snapshot(tmp_path, [orders_method, billing_method])

    result = RUNNER.invoke(
        app,
        ["analyze", "methods", "--module", "billing", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["method_count"] == 1
    assert payload["methods"][0]["id"] == "billing-method"


def test_methods_lists_inheritance_edges_to_concrete_implementations(
    tmp_path: Path,
) -> None:
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders/OrderService.java").write_text(
        "package orders; public interface OrderService { void reserve(String value); }\n",
    )
    (tmp_path / "orders/OrderServiceImpl.java").write_text(
        "package orders; public class OrderServiceImpl implements OrderService { "
        "public void reserve(String value) {} }\n",
    )
    interface_method = IntegrationMethod(
        "interface", "orders", "orders.OrderService.reserve",
        "orders/OrderService.java", 1, 1, (), (),
    )
    implementation = IntegrationMethod(
        "implementation", "orders", "orders.OrderServiceImpl.reserve",
        "orders/OrderServiceImpl.java", 1, 1, (), (),
    )
    _given_persisted_snapshot(tmp_path, [interface_method, implementation])

    result = RUNNER.invoke(
        app,
        ["analyze", "methods", "--module", "orders", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["inheritance_edge_count"] == 1
    assert payload["inheritance_edges"][0]["base"]["id"] == "interface"
    assert payload["inheritance_edges"][0]["implementation"]["id"] == "implementation"


def test_call_edge_text_output_shows_an_edge_used_by_a_flow(tmp_path: Path) -> None:
    # Given an exact edge already used by a persisted IN-to-OUT flow.
    caller = _method("caller", "orders.Controller.create", inputs=("in",))
    callee = _method("callee", "orders.Service.reserve", outputs=("out",))
    edge = CodeQLCallGraphEdge(
        "caller", "callee", "src/Controller.java", 15, "exact",
    )
    flow = CodeFlow(
        "flow-1",
        "orders",
        caller.qualified_method,
        caller.path,
        10,
        20,
        "potential",
        "medium",
        "test",
        (
            CodeFlowStep(1, "http_entry", "POST /orders", caller.path, 10, 10, "in"),
            CodeFlowStep(
                2, "method_call", callee.qualified_method, caller.path, 15, 15,
            ),
            CodeFlowStep(
                3, "message_publish", "orders.created", callee.path, 18, 18, "out",
            ),
        ),
    )
    _given_persisted_snapshot(
        tmp_path, [caller, callee], edges=[edge], flows=[flow],
    )

    # When the user diagnoses the call by its persisted method IDs.
    result = RUNNER.invoke(
        app,
        [
            "analyze", "call-edge", caller.id, callee.id,
            "--root", str(tmp_path),
        ],
    )

    # Then the default text output exposes the verdict, evidence, and flow ID.
    assert result.exit_code == 0
    assert "Verdict : edge_used_in_flow" in result.output
    assert "Étage : complete" in result.output
    assert "confidence=exact" in result.output
    assert "Nœud caller : CodeQL=present · AST=present" in result.output
    assert "Nœud callee : CodeQL=present · AST=present" in result.output
    assert "Arc caller -> callee : CodeQL=present · AST=unknown" in result.output
    assert "Flux : flow-1" in result.output


def test_call_edge_reports_when_the_caller_is_outside_every_input_path(
    tmp_path: Path,
) -> None:
    # Given an exact edge whose caller cannot be reached from an indexed IN port.
    caller = _method("caller", "orders.Helper.prepare")
    callee = _method("callee", "orders.Service.reserve", outputs=("out",))
    edge = CodeQLCallGraphEdge(
        "caller", "callee", "src/Helper.java", 15, "exact",
    )
    _given_persisted_snapshot(tmp_path, [caller, callee], edges=[edge])

    # When the user diagnoses the expected call through the CLI.
    exit_code, payload = _invoke_json(
        tmp_path, "Helper.prepare", "Service.reserve",
    )

    # Then the command explains why the edge cannot appear in an IN-to-OUT flow.
    assert exit_code == 0
    assert payload["status"] == "edge_outside_input_path"
    assert payload["stage"] == "flow_reconstruction"
    assert payload["flow_context"] == {
        "caller_reachable_from_input": False,
        "callee_can_reach_output": True,
    }
