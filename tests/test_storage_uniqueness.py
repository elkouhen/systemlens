from pathlib import Path

import pytest

from systemlens.domain.code_flows import (
    CodeQLCallGraphEdge,
    IntegrationMethod,
    PersistedCodeQLMethod,
)
from systemlens.domain.models import (
    ArchitectureRelation,
    ExtractionDiagnostic,
    MessageEndpoint,
)
from systemlens.domain.module_inventory import ModuleDependency
from systemlens.storage.sqlite import Store, StoreError


def _endpoint(identifier: str, line: int) -> MessageEndpoint:
    return MessageEndpoint(
        id=identifier,
        role="produce",
        system="kafka",
        topic="orders.created",
        topic_dynamic=False,
        source="code",
        framework="test",
        path="Orders.java",
        start_line=line,
        end_line=line,
        snippet="send(order)",
    )


def test_storage_preserves_duplicate_generated_ids(tmp_path: Path) -> None:
    relation = ArchitectureRelation(
        id="same", source_kind="module", source_name="orders", relation="depends_on",
        target_kind="module", target_name="billing", origin="derived", confidence="high",
    )
    method = IntegrationMethod(
        id="same", module="orders", qualified_method="Orders.send",
        path="Orders.java", start_line=1, end_line=2,
        input_endpoint_ids=(), output_endpoint_ids=(),
    )
    with Store(tmp_path) as store:
        store.replace_endpoints_for_files([], [_endpoint("same", 1), _endpoint("same", 2)])
        store.replace_architecture_relations([relation, relation.__class__(
            **{**relation.__dict__, "target_name": "payments"}
        )])
        store.replace_integration_methods([method, method.__class__(
            **{**method.__dict__, "qualified_method": "Orders.receive"}
        )])

        assert len(store.all_endpoints()) == 2
        assert len(store.all_architecture_relations()) == 2
        assert len(store.all_integration_methods()) == 2


def test_storage_deduplicates_exact_composite_rows(tmp_path: Path) -> None:
    dependency = ModuleDependency("orders", "billing")
    edge = CodeQLCallGraphEdge("caller", "callee", "Orders.java", 10, "exact", False)
    diagnostic = ExtractionDiagnostic("Orders.java", "java", "parse_failed", "warning", "partial")
    with Store(tmp_path) as store:
        store.replace_module_dependencies([dependency, dependency])
        store.replace_codeql_call_edges([edge, edge])
        store.replace_extraction_diagnostics_for_files(["Orders.java"], [diagnostic, diagnostic])

        assert store.all_module_dependencies() == [dependency]
        assert store.all_codeql_call_edges() == [edge]
        assert store.all_extraction_diagnostics() == [diagnostic]


def test_storage_persists_codeql_method_projection(tmp_path: Path) -> None:
    method = PersistedCodeQLMethod(
        id="codeql:helper",
        module="orders",
        qualified_method="orders.Service.helper",
        path="orders/Service.java",
        start_line=12,
        end_line=16,
    )
    with Store(tmp_path) as store:
        store.replace_codeql_methods([method])

        assert store.all_codeql_methods() == [method]


def test_storage_rejects_conflicting_contract_rows_before_replacing_snapshot(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        store.replace_openapi_contracts([{"module": "orders", "path": "api.yaml", "spec": {"openapi": "3.0.0"}}])
        with pytest.raises(StoreError, match="Conflicting duplicate OpenAPI contract"):
            store.replace_openapi_contracts([
                {"module": "orders", "path": "api.yaml", "spec": {"openapi": "3.0.0"}},
                {"module": "orders", "path": "api.yaml", "spec": {"openapi": "3.1.0"}},
            ])
        assert store.all_openapi_contracts()[0]["spec"]["openapi"] == "3.0.0"
