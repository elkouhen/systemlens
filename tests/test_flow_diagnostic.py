from systemlens.application.flow_diagnostic import diagnose_flows
from systemlens.application.architecture_inventory import AnalysisProfile, ArchitectureInventory
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep, IntegrationMethod
from systemlens.domain.models import MessageEndpoint


def _endpoint(endpoint_id: str, role: str, system: str, topic: str, module: str) -> MessageEndpoint:
    return MessageEndpoint(endpoint_id, role, system, topic, False, "code", "test", "src/App.java", 1, 2, "", module, "App", None)


def _inventory(endpoints, methods=(), flows=()):
    return ArchitectureInventory(
        endpoints_by_service={}, endpoints_by_module={}, findings_by_service={}, endpoints=list(endpoints),
        findings=[], modules=[], modules_by_service={}, module_dependencies=[], relations=[], diagnostics=[],
        warnings=[], source_roots=[], profile=AnalysisProfile(), code_flows=list(flows),
        integration_methods=list(methods),
    )


def test_diagnose_flows_classifies_missing_global_composition():
    entry = _endpoint("entry", "serve", "rest", "POST /orders", "orders")
    effect = _endpoint("effect", "produce", "kafka", "orders.created", "orders")
    consumer = _endpoint("consumer", "consume", "kafka", "orders.created", "billing")
    method = IntegrationMethod("method", "orders", "OrderController.create", "src/App.java", 1, 10, ("entry",), ("effect",))
    flow = CodeFlow("flow", "orders", "OrderController.create", "src/App.java", 1, 10, "potential", "medium", "test", (
        CodeFlowStep(1, "http_entry", entry.topic, entry.path, 1, 1, "entry"),
        CodeFlowStep(2, "message_publish", effect.topic, effect.path, 2, 2, "effect"),
    ))

    result = diagnose_flows(_inventory([entry, effect, consumer], [method], [flow]))

    assert result["summary"]["local_flows"] == 1
    assert result["summary"]["global_flows"] == 0
    orders_entry = next(item for item in result["entries"] if item["endpoint_id"] == "entry")
    assert orders_entry["status"] == "global_composition_gap"


def test_diagnose_flows_reports_entry_without_method_fact():
    entry = _endpoint("entry", "serve", "rest", "GET /orders", "orders")
    result = diagnose_flows(_inventory([entry]))
    assert result["entries"][0]["status"] == "entry_without_method_fact"
