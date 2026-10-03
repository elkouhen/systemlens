"""Regression coverage for multi-module dispatch and partial CodeQL answers."""

from dataclasses import replace
from collections import deque
from pathlib import Path
import shutil

import pytest

from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.indexing.code_flows import materialize_codeql_code_flows
from systemlens.indexing.codeql import (
    CodeQLCall, CodeQLReachability, automatic_codeql_database,
    extract_codeql_calls, extract_codeql_reachability,
)
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing.java_symbols import JavaSymbols


def _project(root: Path, sources: dict[str, str]):
    for path, source in sources.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source)
    modules = [DiscoveredModule(name, root / name, "maven", None, "library", False, "")
               for name in sorted({path.split("/")[0] for path in sources})]
    methods = materialize_integration_methods(root, [], list(sources), modules)
    endpoints = []
    updated = []
    for method in methods:
        name = method.qualified_method.rsplit(".", 1)[-1]
        if name in {"entry", "output", "otherOutput"}:
            identifier = method.id
            role = "serve" if name == "entry" else "call"
            endpoints.append(MessageEndpoint(
                id=identifier, role=role, system="rest", topic=f"GET /{identifier}",
                topic_dynamic=False, source="code", framework="test", path=method.path,
                start_line=method.start_line, end_line=method.end_line, snippet="",
                module=method.module, qualified_name=method.qualified_method.rsplit(".", 1)[0],
            ))
            method = replace(method, input_endpoint_ids=(identifier,) if name == "entry" else (),
                             output_endpoint_ids=() if name == "entry" else (identifier,))
        updated.append(method)
    return updated, endpoints


SOURCES = {
    "api/Base.java": "package api; public abstract class Base { public abstract void execute(String value); }",
    "api/Middle.java": "package api; public abstract class Middle extends Base {}",
    "web/Controller.java": """package web;
import api.Base;
public class Controller {
  Base service;
  public void entry(String value) { service.execute(value); }
}
""",
    "impl/Concrete.java": """package impl;
import api.Middle;
public class Concrete extends Middle {
  public void execute(String value) { output(); }
  void output() {}
}
""",
    "unrelated/Other.java": """package unrelated;
public class Other {
  public void execute(String value) { otherOutput(); }
  void otherOutput() {}
}
""",
}


def test_ast_cross_module_abstract_dispatch_through_empty_base_and_helper(tmp_path: Path):
    methods, endpoints = _project(tmp_path, SOURCES)
    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path, source_paths=list(SOURCES))
    assert len(flows) == 1
    assert flows[0].steps[-1].path == "impl/Concrete.java"
    assert flows[0].confidence == "low"
    assert [step.name for step in flows[0].steps[1:-1]] == ["impl.Concrete.execute", "impl.Concrete.output"]


def test_ast_interface_field_dispatch_reaches_same_module_output(tmp_path: Path):
    sources = {
        "orders/OrderService.java": """package orders;
public interface OrderService { void reserve(String value); }
""",
        "orders/OrderServiceImpl.java": """package orders;
public class OrderServiceImpl implements OrderService {
  public void reserve(String value) { output(); }
  void output() {}
}
""",
        "orders/OrderController.java": """package orders;
public class OrderController {
  private OrderService service;
  public void entry(String value) { service.reserve(value); }
}
""",
    }
    methods, endpoints = _project(tmp_path, sources)

    flows = materialize_codeql_code_flows(
        methods,
        endpoints,
        [],
        repo_root=tmp_path,
        source_paths=list(sources),
    )

    assert len(flows) == 1
    assert [step.name for step in flows[0].steps[1:-1]] == [
        "orders.OrderServiceImpl.reserve",
        "orders.OrderServiceImpl.output",
    ]


def test_ast_fallback_keeps_other_calls_on_same_line_and_output_methods(tmp_path: Path):
    sources = {
        "orders/OrderController.java": """package orders;
public class OrderController {
  public void entry(String value) { helper(); output(); }
  void helper() {}
  void output() {}
}
""",
    }
    methods, endpoints = _project(tmp_path, sources)
    caller = next(method for method in methods if method.qualified_method.endswith("OrderController.entry"))
    helper = next(method for method in methods if method.qualified_method.endswith("OrderController.helper"))
    calls = [CodeQLCall(
        caller.qualified_method, caller.path, caller.start_line,
        helper.qualified_method, helper.path, helper.start_line,
        caller.start_line, "exact",
    )]

    flows = materialize_codeql_code_flows(
        methods, endpoints, calls, repo_root=tmp_path, source_paths=list(sources),
    )

    assert any(
        [step.name for step in flow.steps[1:-1]] == [
            "orders.OrderController.output",
        ]
        for flow in flows
    )


def test_ast_generic_interface_dispatch_reaches_same_module_output(tmp_path: Path):
    sources = {
        "orders/Sender.java": """package orders;
public interface Sender<T> { void send(T value); }
""",
        "orders/SenderImpl.java": """package orders;
public class SenderImpl implements Sender<String> {
  public void send(String value) { output(); }
  void output() {}
}
""",
        "orders/OrderController.java": """package orders;
public class OrderController {
  private final Sender<String> sender;
  public void entry(String value) { sender.send(value); }
}
""",
    }
    methods, endpoints = _project(tmp_path, sources)

    flows = materialize_codeql_code_flows(
        methods, endpoints, [], repo_root=tmp_path, source_paths=list(sources),
    )

    assert any(
        [step.name for step in flow.steps[1:-1]] == [
            "orders.SenderImpl.send",
            "orders.SenderImpl.output",
        ]
        for flow in flows
    )


def test_integration_methods_include_plain_methods_in_repository_root_module(tmp_path: Path):
    source_path = "src/main/java/orders/OrderController.java"
    destination = tmp_path / source_path
    destination.parent.mkdir(parents=True)
    destination.write_text("""package orders;
public class OrderController {
  public void entry(String value) { helper(); }
  void helper() {}
}
""")
    root_module = DiscoveredModule(
        "application", tmp_path, "maven", None, "library", False, "",
    )

    methods = materialize_integration_methods(
        tmp_path, [], [source_path], [root_module],
    )

    assert {method.qualified_method for method in methods} == {
        "orders.OrderController.entry",
        "orders.OrderController.helper",
    }


def test_ast_spring_style_sender_and_producer_interfaces_reach_three_kafka_outputs(
    tmp_path: Path,
):
    sources = {
        "orders/MessageSender.java": """package orders;
public interface MessageSender {
  void sendOrder(String value);
  void sendStock(String value);
  void sendAudit(String value);
}
""",
        "orders/OrderProducer.java": """package orders;
public interface OrderProducer { void output(String value); }
""",
        "orders/StockProducer.java": """package orders;
public interface StockProducer { void output(String value); }
""",
        "orders/AuditProducer.java": """package orders;
public interface AuditProducer { void output(String value); }
""",
        "orders/MessageSenderImpl.java": """package orders;
import org.springframework.stereotype.Component;
import lombok.RequiredArgsConstructor;
@RequiredArgsConstructor
@Component
public class MessageSenderImpl implements MessageSender {
  private final OrderProducer orderProducer;
  private final StockProducer stockProducer;
  private final AuditProducer auditProducer;
  public void sendOrder(String value) { orderProducer.output(value); }
  public void sendStock(String value) { stockProducer.output(value); }
  public void sendAudit(String value) { auditProducer.output(value); }
}
""",
        "orders/OrderProducerImpl.java": """package orders;
import org.springframework.stereotype.Component;
@Component
public class OrderProducerImpl implements OrderProducer {
  public void output(String value) {}
}
""",
        "orders/StockProducerImpl.java": """package orders;
import org.springframework.stereotype.Component;
@Component
public class StockProducerImpl implements StockProducer {
  public void output(String value) {}
}
""",
        "orders/AuditProducerImpl.java": """package orders;
import org.springframework.stereotype.Component;
@Component
public class AuditProducerImpl implements AuditProducer {
  public void output(String value) {}
}
""",
        "orders/OrderHandler.java": """package orders;
import org.springframework.stereotype.Component;
import lombok.RequiredArgsConstructor;
@RequiredArgsConstructor
@Component
public class OrderHandler {
  private final MessageSender sender;
  public void entry(String value) {
    sender.sendOrder(value);
    sender.sendStock(value);
    sender.sendAudit(value);
  }
}
""",
    }
    methods, endpoints = _project(tmp_path, sources)

    flows = materialize_codeql_code_flows(
        methods,
        endpoints,
        [],
        repo_root=tmp_path,
        source_paths=list(sources),
    )

    assert {flow.steps[-1].path for flow in flows} == {
        "orders/OrderProducerImpl.java",
        "orders/StockProducerImpl.java",
        "orders/AuditProducerImpl.java",
    }


def test_template_method_in_base_dispatches_to_cross_module_override(tmp_path: Path):
    sources = {**SOURCES,
               "api/Base.java": """package api;
public abstract class Base {
  public void process(String value) { execute(value); }
  public abstract void execute(String value);
}
""",
               "web/Controller.java": SOURCES["web/Controller.java"].replace("service.execute(value)", "service.process(value)")}
    methods, endpoints = _project(tmp_path, sources)
    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path, source_paths=list(sources))
    assert len(flows) == 1
    assert [step.name for step in flows[0].steps[1:-1]] == [
        "api.Base.process", "impl.Concrete.execute", "impl.Concrete.output",
    ]


def test_kafka_listener_reaches_inherited_on_consume_message_then_consume(tmp_path: Path):
    sources = {
        "api/BaseConsumer.java": """package api;
public class BaseConsumer {
  public void onConsumeMessage(String value) { consume(value); }
  public void consume(String value) {}
}
""",
        "impl/Consumer.java": """package impl;
import api.BaseConsumer;
public class Consumer extends BaseConsumer {
  public void entry(String value) { onConsumeMessage(value); }
  @Override public void consume(String value) { output(); }
  void output() {}
}
""",
    }
    methods, endpoints = _project(tmp_path, sources)

    flows = materialize_codeql_code_flows(
        methods, endpoints, [], repo_root=tmp_path, source_paths=list(sources)
    )

    assert len(flows) == 1
    assert [step.name for step in flows[0].steps[1:-1]] == [
        "api.BaseConsumer.onConsumeMessage",
        "impl.Consumer.consume",
        "impl.Consumer.output",
    ]


def test_duplicate_qualified_types_across_modules_remain_unresolved(tmp_path: Path):
    sources = {**SOURCES, "duplicate/Concrete.java": SOURCES["impl/Concrete.java"]}
    methods, endpoints = _project(tmp_path, sources)
    assert materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path, source_paths=list(sources)) == []


def test_declared_receiver_override_hides_ancestor_body(tmp_path: Path):
    sources = {
        "api/Base.java": """package api;
public class Base {
  public void execute() { otherOutput(); }
  void otherOutput() {}
}
""",
        "impl/Concrete.java": """package impl;
public class Concrete extends api.Base {
  public void execute() { output(); }
  void output() {}
}
""",
        "web/Controller.java": "package web; public class Controller { impl.Concrete service; public void entry() { service.execute(); } }",
    }
    methods, endpoints = _project(tmp_path, sources)
    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path, source_paths=list(sources))
    assert len(flows) == 1
    assert flows[0].steps[-1].path == "impl/Concrete.java"


def test_abstract_bridge_checks_signature_and_hierarchy(tmp_path: Path):
    methods, endpoints = _project(tmp_path, SOURCES)
    entry = next(method for method in methods if method.input_endpoint_ids)
    contract = next(method for method in methods if method.qualified_method == "api.Base.execute")
    call = CodeQLCall(entry.qualified_method, entry.path, entry.start_line,
                      contract.qualified_method, contract.path, contract.start_line, entry.start_line, "possible")
    flows = materialize_codeql_code_flows(methods, endpoints, [call], repo_root=tmp_path, source_paths=list(SOURCES))
    assert len(flows) == 1
    assert flows[0].steps[-1].path == "impl/Concrete.java"


@pytest.mark.parametrize("implementation", [
    "package impl; public class Concrete { public void execute(String v) { output(); } void output() {} }",
    "package impl; public class Concrete extends api.Base { public void execute(int v) { output(); } void output() {} }",
])
def test_unrelated_or_incompatible_method_does_not_bridge(tmp_path: Path, implementation: str):
    sources = {**SOURCES, "impl/Concrete.java": implementation}
    methods, endpoints = _project(tmp_path, sources)
    entry = next(method for method in methods if method.input_endpoint_ids)
    contract = next(method for method in methods if method.qualified_method == "api.Base.execute")
    call = CodeQLCall(entry.qualified_method, entry.path, entry.start_line,
                      contract.qualified_method, contract.path, contract.start_line, entry.start_line, "possible")
    assert materialize_codeql_code_flows(methods, endpoints, [call], repo_root=tmp_path, source_paths=list(sources)) == []


def test_multiple_real_implementations_remain_unresolved(tmp_path: Path):
    sources = {**SOURCES, "impl/Second.java":
               "package impl; public class Second extends api.Base { public void execute(String v) { otherOutput(); } void otherOutput() {} }"}
    methods, endpoints = _project(tmp_path, sources)
    assert materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path, source_paths=list(sources)) == []


def test_qualified_import_does_not_merge_same_simple_type_names(tmp_path: Path):
    sources = {**SOURCES, "unrelated/Base.java": "package unrelated; public abstract class Base { public abstract void execute(String v); }",
               "unrelated/Other.java": "package unrelated; public class Other extends Base { public void execute(String v) { otherOutput(); } void otherOutput() {} }"}
    methods, endpoints = _project(tmp_path, sources)
    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path, source_paths=list(sources))
    assert len(flows) == 1
    assert flows[0].steps[-1].path == "impl/Concrete.java"


def test_partial_reachability_keeps_fallback_and_direct_pair(tmp_path: Path):
    methods, endpoints = _project(tmp_path, SOURCES)
    entry = next(method for method in methods if method.input_endpoint_ids)
    target = next(method for method in methods if method.qualified_method == "unrelated.Other.otherOutput")
    relation = CodeQLReachability(entry.qualified_method, entry.path, entry.start_line,
                                  target.qualified_method, target.path, target.start_line, "medium")
    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path,
                                        source_paths=list(SOURCES), reachability=[relation])
    assert {flow.steps[-1].path for flow in flows} == {"impl/Concrete.java", "unrelated/Other.java"}
    direct = next(flow for flow in flows if flow.steps[-1].path == target.path)
    assert direct.confidence == "medium"
    assert len(direct.steps) == 2


def test_direct_proof_replaces_fallback_without_synthetic_witness(tmp_path: Path):
    methods, endpoints = _project(tmp_path, SOURCES)
    entry = next(method for method in methods if method.input_endpoint_ids)
    target = next(method for method in methods if method.qualified_method == "impl.Concrete.output")
    relation = CodeQLReachability(entry.qualified_method, entry.path, entry.start_line,
                                  target.qualified_method, target.path, target.start_line, "medium")
    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path,
                                        source_paths=list(SOURCES), reachability=[relation])
    assert len(flows) == 1
    assert flows[0].confidence == "medium"
    assert len(flows[0].steps) == 2


def test_symbols_parse_each_indexed_file_once_and_respect_perimeter(tmp_path: Path, monkeypatch):
    methods, _endpoints = _project(tmp_path, SOURCES)
    outside = tmp_path / "ignored/Concrete.java"
    outside.parent.mkdir()
    outside.write_text(SOURCES["impl/Concrete.java"])
    from systemlens.indexing.java_symbols import parser
    parse = parser.parse_java
    paths = []

    def counted(root, path):
        paths.append(path)
        return parse(root, path)

    monkeypatch.setattr(parser, "parse_java", counted)
    symbols = JavaSymbols(tmp_path, methods, list(SOURCES))
    assert sorted(paths) == sorted(SOURCES)
    assert symbols.bridge(next(method for method in methods if method.qualified_method == "api.Base.execute")) is not None


def test_many_direct_outputs_share_one_predecessor_search(tmp_path: Path, monkeypatch):
    methods, endpoints = _project(tmp_path, SOURCES)
    entry = next(method for method in methods if method.input_endpoint_ids)
    template = next(method for method in methods if method.qualified_method == "impl.Concrete.output")
    endpoint = next(endpoint for endpoint in endpoints if endpoint.id in template.output_endpoint_ids)
    targets = [replace(template, id=f"method-{i}", qualified_method=f"impl.Sink{i}.output",
                       path=f"impl/Sink{i}.java", output_endpoint_ids=(f"output-{i}",)) for i in range(100)]
    target_endpoints = [replace(endpoint, id=method.output_endpoint_ids[0], path=method.path) for method in targets]
    calls = [CodeQLCall(entry.qualified_method, entry.path, entry.start_line,
                        target.qualified_method, target.path, target.start_line, entry.start_line) for target in targets]
    relations = [CodeQLReachability(entry.qualified_method, entry.path, entry.start_line,
                                    target.qualified_method, target.path, target.start_line, "medium") for target in targets]
    popped = 0

    class CountedDeque(deque):
        def popleft(self):
            nonlocal popped
            popped += 1
            return super().popleft()

    monkeypatch.setattr("systemlens.indexing.code_flows.deque", CountedDeque)
    stats = {}
    flows = materialize_codeql_code_flows(
        [entry, *targets], [*endpoints, *target_endpoints], calls,
        reachability=relations, stats=stats,
    )
    assert len(flows) == 100
    assert all(len(flow.steps) == 3 for flow in flows)
    assert popped <= 2 * len(targets) + 4
    assert stats["explored_paths"] >= 100


def test_medium_proof_never_uses_shorter_possible_dispatch_route(tmp_path: Path):
    methods, endpoints = _project(tmp_path, SOURCES)
    entry = next(method for method in methods if method.input_endpoint_ids)
    helper = next(method for method in methods if method.qualified_method == "impl.Concrete.execute")
    target = next(method for method in methods if method.qualified_method == "impl.Concrete.output")

    def call(source, target, confidence="exact"):
        return CodeQLCall(source.qualified_method, source.path, source.start_line,
                          target.qualified_method, target.path, target.start_line, source.start_line, confidence)

    relation = CodeQLReachability(entry.qualified_method, entry.path, entry.start_line,
                                  target.qualified_method, target.path, target.start_line, "medium")
    flows = materialize_codeql_code_flows(methods, endpoints,
                                        [call(entry, target, "possible"), call(entry, helper), call(helper, target)],
                                        reachability=[relation])
    assert len(flows) == 1
    assert flows[0].confidence == "medium"
    assert [step.name for step in flows[0].steps[1:-1]] == [helper.qualified_method, target.qualified_method]


def test_this_field_ignores_a_shadowing_parameter(tmp_path: Path):
    sources = {**SOURCES, "web/Controller.java": """package web;
import api.Base;
public class Controller {
  Base service;
  public void entry(unrelated.Other service) { this.service.execute("value"); }
}
"""}
    methods, endpoints = _project(tmp_path, sources)
    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path, source_paths=list(sources))
    assert len(flows) == 1
    assert flows[0].steps[-1].path == "impl/Concrete.java"


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("codeql") is None, reason="Local CodeQL is required")
def test_real_codeql_cross_module_inheritance_and_csv_contract(tmp_path: Path):
    sources = {
        "api/Base.java": "package api; public abstract class Base { public abstract void output(); }",
        "impl/Concrete.java": "package impl; public class Concrete extends api.Base { public void output() {} }",
        "web/Controller.java": "package web; public class Controller { public void entry() { api.Base value = new impl.Concrete(); value.output(); } }",
    }
    methods, endpoints = _project(tmp_path, sources)
    with automatic_codeql_database(tmp_path, timeout_seconds=180, threads=2) as database:
        assert database is not None
        calls = extract_codeql_calls(database, timeout_seconds=180, threads=2)
        relations = extract_codeql_reachability(database, methods, timeout_seconds=180, threads=2)
    assert any(call.callee == "impl.Concrete.output" for call in calls)
    assert any(relation.target == "impl.Concrete.output" for relation in relations)
    flows = materialize_codeql_code_flows(methods, endpoints, calls, reachability=relations)
    assert any(flow.steps[-1].path == "impl/Concrete.java" for flow in flows)


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("codeql") is None, reason="Local CodeQL is required")
def test_real_codeql_interface_field_dispatch_in_same_module(tmp_path: Path):
    sources = {
        "orders/OrderService.java": """package orders;
public interface OrderService { void reserve(String value); }
""",
        "orders/OrderServiceImpl.java": """package orders;
public class OrderServiceImpl implements OrderService {
  public void reserve(String value) { output(); }
  void output() {}
}
""",
        "orders/OrderController.java": """package orders;
public class OrderController {
  private OrderService service;
  public void entry(String value) { service.reserve(value); }
}
""",
    }
    methods, endpoints = _project(tmp_path, sources)
    with automatic_codeql_database(tmp_path, timeout_seconds=180, threads=2) as database:
        assert database is not None
        calls = extract_codeql_calls(database, timeout_seconds=180, threads=2)
        relations = extract_codeql_reachability(database, methods, timeout_seconds=180, threads=2)

    assert any(
        call.caller == "orders.OrderController.entry"
        and call.callee.endswith(("OrderService.reserve", "OrderServiceImpl.reserve"))
        for call in calls
    )
    flows = materialize_codeql_code_flows(
        methods, endpoints, calls, reachability=relations,
        repo_root=tmp_path, source_paths=list(sources),
    )
    assert any(
        [step.name for step in flow.steps[1:-1]] == [
            "orders.OrderServiceImpl.reserve",
            "orders.OrderServiceImpl.output",
        ]
        for flow in flows
    )
