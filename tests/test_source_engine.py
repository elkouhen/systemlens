from pathlib import Path

from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing.java_symbols import JavaSymbols

_SOURCE = """package p;
import java.util.List;
class Service {
  void field() {}
  void local() {}
  void param() {}
  void varLocal() {}
  void lambda() {}
  void ref(String s) {}
  Service self() { return this; }
  void chained() {}
  static void util() {}
  void overload(String s) {}
  void overload(Integer i) {}
  void multi() {}
  void multi(String s) {}
}
interface Port { void send(); void close(); }
class Adapter implements Port { public void send() {} public void close() {} }
class Other implements Port { public void send() {} public void close() {} }
class Base { void inherited() {} }
class Patterns extends Base {
  private Service service;
  private Port port;
  void run(Service param, List<String> items, Object unknown) {
    service.field();
    Service local = new Service(); local.local();
    param.param();
    var v = new Service(); v.varLocal();
    items.forEach(i -> service.lambda());
    items.forEach(service::ref);
    service.self().chained();
    Service.util();
    service.overload("x");
    port.send();
    inherited();
    service.overload(unknown);
    items.forEach(service::multi);
  }
}
"""


def _calls(tmp_path: Path) -> dict[str, list[tuple[int, str]]]:
    """Calls from ``Patterns.run`` by callee name: (callee line, dispatch)."""
    path = "app/src/main/java/p/Patterns.java"
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(_SOURCE, encoding="utf-8")
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(tmp_path, [], [path], [module])
    symbols = JavaSymbols(tmp_path, methods, [path])
    calls: dict[str, list[tuple[int, str]]] = {}
    for call in symbols.fallback_calls(set()):
        if call.caller.endswith("Patterns.run"):
            calls.setdefault(call.callee.rsplit(".", 1)[-1], []).append(
                (call.callee_line, call.dispatch_confidence)
            )
    return calls


def _line(text: str) -> int:
    return next(number for number, line in enumerate(_SOURCE.splitlines(), start=1) if text in line)


def test_source_engine_resolves_common_spring_call_shapes(tmp_path: Path) -> None:
    calls = _calls(tmp_path)

    for name in ("field", "local", "param", "lambda", "chained", "self"):
        assert calls[name] == [(_line(f"{name}()"), "exact")], name
    assert calls["varLocal"] == [(_line("void varLocal"), "exact")], "var takes its initializer type"
    assert calls["ref"] == [(_line("void ref"), "exact")], "a method reference is a call"
    assert calls["util"] == [(_line("static void util"), "exact")], "a type name is a static call"
    assert calls["overload"] == [(_line("overload(String s)"), "exact")], (
        "a String literal selects overload(String); the unknown argument selects nothing"
    )
    assert calls["inherited"] == [(_line("void inherited"), "exact")], "an ancestor's body is exact"


def test_source_engine_keeps_doubt_where_syntax_cannot_decide(tmp_path: Path) -> None:
    calls = _calls(tmp_path)

    # Two source implementations of an interface stay possible.
    assert sorted(calls["send"]) == [(_line("class Adapter"), "possible"), (_line("class Other"), "possible")]
    # A method reference to a name declared with two arities stays unresolved.
    assert "multi" not in calls


def test_an_override_typed_through_an_external_wildcard_import_is_dispatched(tmp_path: Path) -> None:
    files = {
        "app/src/main/java/p/Port.java": (
            "package p;\nimport org.springframework.http.HttpHeaders;\n"
            "interface Port { String all(HttpHeaders headers); }\n"
        ),
        "app/src/main/java/p/Adapter.java": (
            "package p;\nimport org.springframework.http.*;\n"
            "class Adapter implements Port {\n"
            "  public String all(HttpHeaders headers) { return check(headers); }\n"
            "  String check(HttpHeaders headers) { return \"\"; }\n"
            "}\n"
        ),
        "app/src/main/java/p/Controller.java": (
            "package p;\nimport org.springframework.http.HttpHeaders;\n"
            "class Controller {\n"
            "  private Port port;\n"
            "  String run(HttpHeaders headers) { return port.all(headers); }\n"
            "}\n"
        ),
    }
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(tmp_path, [], list(files), [module])
    symbols = JavaSymbols(tmp_path, methods, list(files))

    edges = {(call.caller.rsplit(".", 2)[-2:][1], call.callee, call.dispatch_confidence)
             for call in symbols.fallback_calls(set())}
    closed = {(call.caller.rsplit(".", 2)[-2:][1], call.callee, call.dispatch_confidence)
              for call in symbols.fallback_calls(set(), closed_world=True)}

    # ``HttpHeaders`` stays unresolved in Adapter, yet the simple name
    # matches the one contract the interface declares.
    assert ("run", "p.Adapter.all", "possible") in edges
    # As the whole call graph, a unique source implementation is exact.
    assert ("run", "p.Adapter.all", "exact") in closed
    # A concrete body with an unresolved parameter still implements itself.
    assert ("all", "p.Adapter.check", "exact") in edges


def test_a_runtime_implemented_repository_method_is_a_possible_target(tmp_path: Path) -> None:
    path = "app/src/main/java/p/Orders.java"
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(
        "package p;\n"
        "import org.springframework.data.jpa.repository.JpaRepository;\n"
        "interface OrderRepository extends JpaRepository<Order, String> { Order findByOrderId(String id); }\n"
        "class Order {}\n"
        "class OrderService {\n"
        "  private OrderRepository repository;\n"
        "  Order find(String id) { return repository.findByOrderId(id); }\n"
        "  Order missing(String id) { return repository.findById(id); }\n"
        "}\n",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(tmp_path, [], [path], [module])
    symbols = JavaSymbols(tmp_path, methods, [path])

    calls = [(call.caller, call.callee, call.dispatch_confidence)
             for call in symbols.fallback_calls(set(), closed_world=True)]

    # Spring generates the body: the declaration is the only source target.
    assert calls == [("p.OrderService.find", "p.OrderRepository.findByOrderId", "possible")]


def test_an_anonymous_class_body_belongs_to_the_method_creating_it(tmp_path: Path) -> None:
    path = "app/src/main/java/p/Outer.java"
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(
        "package p;\n"
        "class Worker { void work() {} }\n"
        "class Outer {\n"
        "  private Worker worker;\n"
        "  void run() {}\n"
        "  void schedule(Runnable task) {}\n"
        "  void start() {\n"
        "    schedule(new Runnable() { public void run() { worker.work(); run(); } });\n"
        "  }\n"
        "  void call() { Outer other = new Outer(); other.run(); }\n"
        "}\n",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(tmp_path, [], [path], [module])
    symbols = JavaSymbols(tmp_path, methods, [path])

    calls = sorted(
        (call.caller.rsplit(".", 1)[-1], call.callee, call.call_line, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
    )

    # The anonymous ``run`` is no method of ``Outer``.
    assert sorted(method.qualified_method for method in methods) == [
        "p.Outer.call", "p.Outer.run", "p.Outer.schedule", "p.Outer.start", "p.Worker.work",
    ]
    assert calls == [
        ("call", "p.Outer.run", 10, "exact"),
        ("start", "p.Outer.schedule", 8, "exact"),
        # The anonymous body runs on behalf of ``start``; its unqualified
        # ``run()`` names the anonymous instance and stays unresolved.
        ("start", "p.Worker.work", 8, "exact"),
    ]


def test_dispatch_left_unique_by_module_preference_stays_possible(tmp_path: Path) -> None:
    files = {
        "core/src/main/java/p/Port.java": "package p;\npublic interface Port { void send(); }\n",
        "core/src/main/java/p/CoreAdapter.java": (
            "package p;\npublic class CoreAdapter implements Port { public void send() {} }\n"
        ),
        "core/src/main/java/p/Caller.java": (
            "package p;\npublic class Caller { private Port port; void go() { port.send(); } }\n"
        ),
        "app/src/main/java/p/AppAdapter.java": (
            "package p;\npublic class AppAdapter implements Port { public void send() {} }\n"
        ),
    }
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    modules = [
        DiscoveredModule(
            name=name, path=tmp_path / name, build_system="maven", version=None,
            kind="application", starts_application=True, configuration_example="",
        )
        for name in ("core", "app")
    ]
    methods = materialize_integration_methods(tmp_path, [], list(files), modules)
    symbols = JavaSymbols(tmp_path, methods, list(files))

    calls = [(call.callee, call.dispatch_confidence)
             for call in symbols.fallback_calls(set(), closed_world=True)
             if call.caller.endswith("Caller.go")]

    # The caller's module is preferred, but the other implementation exists.
    assert calls == [("p.CoreAdapter.send", "possible")]
