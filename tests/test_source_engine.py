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
interface Port { void send(); }
class Adapter implements Port { public void send() {} }
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

    # Dependencies may add implementations of a source interface.
    assert calls["send"] == [(_line("public void send"), "possible")]
    # A method reference to a name declared with two arities stays unresolved.
    assert "multi" not in calls
