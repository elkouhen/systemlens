from pathlib import Path

from systemlens.domain.module_inventory import DiscoveredModule, ModuleDependency
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
    Runnable later = service::multi;
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
    # A method reference to a name declared with two arities binds to the
    # one its target fixes (BL-24): forEach takes a one-parameter Consumer.
    # The assignment to a Runnable binds the zero-parameter one (BL-37).
    assert sorted(calls["multi"]) == [(_line("void multi()"), "exact"), (_line("void multi(String s)"), "exact")]


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
    # No target is exact, so the declaration is a declared row (BL-28).
    assert calls == [("p.Port.send", "possible"), ("p.CoreAdapter.send", "possible")]


_SCOPED = """package p;
import java.util.List;
class Order { void process() {} void fail() {} }
class Failure extends RuntimeException { void report() {} }
class Resource implements AutoCloseable { void use() {} public void close() {} }
class Svc {
  private Order order;
  Resource open() { return new Resource(); }
  void run(List<Order> orders) {
    for (Order o : orders) { o.process(); }
    try (Resource r = open()) { r.use(); } catch (Failure e) { e.report(); }
    orders.forEach((Order x) -> x.fail());
    java.util.function.Consumer<?> sink = order -> order.fail();
  }
}
"""


def test_variables_declared_by_enclosing_statements_type_their_receivers(tmp_path: Path) -> None:
    path = "app/src/main/java/p/Svc.java"
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(_SCOPED, encoding="utf-8")
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(tmp_path, [], [path], [module])
    symbols = JavaSymbols(tmp_path, methods, [path])

    calls = sorted(
        (call.callee, call.call_line, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller.endswith("Svc.run")
    )

    def line(text: str) -> int:
        return next(number for number, value in enumerate(_SCOPED.splitlines(), 1) if text in value)

    assert calls == [
        ("p.Failure.report", line("catch (Failure"), "exact"),
        ("p.Order.fail", line("(Order x)"), "exact"),
        ("p.Order.process", line("for (Order o"), "exact"),
        ("p.Resource.use", line("try (Resource"), "exact"),
        ("p.Svc.open", line("try (Resource"), "exact"),
    ]
    # The untyped lambda parameter ``order`` shadows the field of the same
    # name, and a wildcard target types no lambda parameter: only the
    # typed lambda ``x`` yields ``fail``.
    assert sum(callee == "p.Order.fail" for callee, _line, _dispatch in calls) == 1


def test_a_statically_imported_source_method_is_a_call(tmp_path: Path) -> None:
    files = {
        "app/src/main/java/p/Util.java": (
            "package p;\npublic class Util { public static void helper() {} }\n"
        ),
        "app/src/main/java/p/Other.java": (
            "package p;\npublic class Other { public static void shared() {} }\n"
        ),
        "app/src/main/java/p/Third.java": (
            "package p;\npublic class Third { public static void shared() {} }\n"
        ),
        "app/src/main/java/q/User.java": (
            "package q;\nimport static p.Util.helper;\nimport static p.Other.*;\n"
            "import static p.Third.*;\n"
            "class User { void go() { helper(); shared(); } }\n"
        ),
        "app/src/main/java/q/Shadow.java": (
            "package q;\nimport static p.Util.helper;\n"
            "class Shadow { void helper(int x) {} void go() { helper(); } }\n"
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

    calls = sorted(
        (call.caller, call.callee, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
    )

    # ``shared`` comes from two static wildcards and stays unresolved; a
    # ``helper`` declared by the caller's own type shadows the import.
    assert calls == [("q.User.go", "p.Util.helper", "exact")]


def test_record_components_type_chained_receivers(tmp_path: Path) -> None:
    path = "app/src/main/java/p/Records.java"
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(
        "package p;\n"
        "class Order { void process() {} }\n"
        "class Audit { void log() {} }\n"
        "record Pair(Order order, Order other) {\n"
        "  public Audit other() { return new Audit(); }\n"
        "}\n"
        "class Svc {\n"
        "  void run(Pair pair) { pair.order().process(); pair.other().log(); }\n"
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
        (call.callee, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller.endswith("Svc.run")
    )

    # The explicit ``other()`` accessor overrides the component's type.
    assert calls == [
        ("p.Audit.log", "exact"),
        ("p.Order.process", "exact"),
        ("p.Pair.other", "exact"),
    ]


def _symbols(tmp_path: Path, files: dict[str, str]) -> JavaSymbols:
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(tmp_path, [], list(files), [module])
    return JavaSymbols(tmp_path, methods, list(files))


def test_an_interface_call_keeps_a_possible_witness_next_to_its_exact_implementation(
    tmp_path: Path,
) -> None:
    symbols = _symbols(tmp_path, {
        "app/src/main/java/p/Svc.java": (
            "package p;\n"
            "interface Port { void send(); static void make() {} default void ping() {} }\n"
            "class Adapter implements Port { public void send() {} }\n"
            "class Svc {\n"
            "  private Port port;\n"
            "  private Adapter adapter;\n"
            "  void run() { port.send(); Port.make(); port.ping(); adapter.send(); }\n"
            "}\n"
        ),
    })

    closed = sorted(
        (call.callee, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller == "p.Svc.run"
    )
    witnesses = {call.callee: exact.callee for call, exact in symbols.interface_witnesses.items()}
    opened = sorted(
        (call.callee, call.dispatch_confidence)
        for call in symbols.fallback_calls(set())
        if call.caller == "p.Svc.run"
    )

    # ADR-70: the contract hop CodeQL keeps beside the exact dispatch. A
    # static or un-overridden default interface method is the exact target
    # itself; a receiver typed by the class names no interface method.
    assert closed == [
        ("p.Adapter.send", "exact"),
        ("p.Adapter.send", "exact"),
        ("p.Port.make", "exact"),
        ("p.Port.ping", "exact"),
        ("p.Port.send", "possible"),
    ]
    assert witnesses == {"p.Port.send": "p.Adapter.send"}
    # Outside a closed world (the CodeQL fallback pass) nothing is added.
    assert ("p.Port.send", "possible") not in opened


def test_an_external_interface_dispatches_to_its_source_implementations(tmp_path: Path) -> None:
    symbols = _symbols(tmp_path, {
        "app/src/main/java/p/Tasks.java": (
            "package p;\n"
            "import java.util.concurrent.*;\n"
            "import org.example.Listener;\n"
            "import org.example.Store;\n"
            "import org.example.Account;\n"
            "class Job implements Callable<String> { public String call() { return \"\"; } }\n"
            "class Poller extends Thread { public void run() {} }\n"
            "class Loop implements Runnable { public void run() {} }\n"
            "class Audit implements Listener {\n"
            "  public void on(String text) {}\n"
            "  public void on(Integer code) {}\n"
            "}\n"
            "class Disk implements Store { public void save(String key) {} }\n"
            "class User implements Account { public String name() { return \"\"; } }\n"
            "class Svc {\n"
            "  void submit(Callable<String> task) throws Exception { task.call(); }\n"
            "  void start(Runnable task) { task.run(); }\n"
            "  void publish(Listener listener, Object value) { listener.on(value); }\n"
            "  void keep(Store store) { store.save(\"k\"); }\n"
            "  void login(Account account) { account.name(); }\n"
            "  Account guest() { return new Account() { public String name() { return \"\"; } }; }\n"
            "}\n"
        ),
    })

    closed = sorted(
        (call.caller.rsplit(".", 1)[-1], call.callee, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller.startswith("p.Svc.")
    )
    opened = [call for call in symbols.fallback_calls(set()) if call.caller.startswith("p.Svc.")]

    # One source body stays possible: a library can supply another (ADR-71).
    # A lambda can implement
    # ``Callable`` and an anonymous class implements ``Account``: both stay
    # possible. A ``Thread`` subclass is a ``Runnable``. Two same-arity
    # overloads that the argument cannot separate leave the call unresolved.
    assert closed == [
        ("keep", "p.Disk.save", "possible"),
        ("login", "p.User.name", "possible"),
        ("start", "p.Loop.run", "possible"),
        ("start", "p.Poller.run", "possible"),
        ("submit", "p.Job.call", "possible"),
    ]
    assert opened == []


def _symbols_in_modules(
    tmp_path: Path, files: dict[str, str], dependencies: list[ModuleDependency],
) -> JavaSymbols:
    """Symbols over one module per top-level directory of ``files``."""
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    modules = [
        DiscoveredModule(
            name=name, path=tmp_path / name, build_system="maven", version=None,
            kind="application", starts_application=True, configuration_example="",
        )
        for name in sorted({path.split("/", 1)[0] for path in files})
    ]
    methods = materialize_integration_methods(tmp_path, [], list(files), modules)
    return JavaSymbols(tmp_path, methods, list(files), module_dependencies=dependencies)


def _external_calls(symbols: JavaSymbols, caller_prefix: str) -> list[tuple[str, str]]:
    return sorted(
        (call.callee, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller.startswith(caller_prefix)
    )


_POOL = (
    "package {package};\n"
    "import java.util.concurrent.*;\n"
    "public class Pool implements ExecutorService {{\n"
    "  public void shutdown() {{}}\n"
    "}}\n"
)


def test_an_external_interface_dispatches_only_into_visible_modules(tmp_path: Path) -> None:
    symbols = _symbols_in_modules(tmp_path, {
        "first/src/main/java/a/Pool.java": _POOL.format(package="a"),
        "first/src/main/java/a/Svc.java": (
            "package a;\n"
            "import java.util.concurrent.*;\n"
            "class Svc {\n"
            "  private ExecutorService pool;\n"
            "  void stop() { pool.shutdown(); }\n"
            "}\n"
        ),
        "second/src/main/java/b/Pool.java": _POOL.format(package="b"),
    }, [])

    # ADR-71: ``second`` is not on the classpath of ``first``.
    assert _external_calls(symbols, "a.Svc.") == [("a.Pool.shutdown", "possible")]


def test_a_library_never_dispatches_into_a_module_that_depends_on_it(tmp_path: Path) -> None:
    symbols = _symbols_in_modules(tmp_path, {
        "lib/src/main/java/l/Chain.java": (
            "package l;\n"
            "import java.util.concurrent.Callable;\n"
            "public class Chain implements Callable<String> {\n"
            "  private Callable<String> next;\n"
            "  public String call() throws Exception { return next.call(); }\n"
            "}\n"
        ),
        "svc/src/main/java/s/Job.java": (
            "package s;\n"
            "import java.util.concurrent.Callable;\n"
            "class Job implements Callable<String> { public String call() { return \"\"; } }\n"
        ),
        "svc/src/main/java/s/Run.java": (
            "package s;\n"
            "import java.util.concurrent.Callable;\n"
            "class Run { void go(Callable<String> task) throws Exception { task.call(); } }\n"
        ),
    }, [ModuleDependency("svc", "lib")])

    # The library's own body is visible to it; the service's is not. The
    # service still dispatches into the library it depends on.
    assert _external_calls(symbols, "l.Chain.") == [("l.Chain.call", "possible")]
    assert _external_calls(symbols, "s.Run.") == [
        ("l.Chain.call", "possible"), ("s.Job.call", "possible"),
    ]


def test_a_single_source_body_of_an_external_interface_stays_possible(tmp_path: Path) -> None:
    symbols = _symbols_in_modules(tmp_path, {
        "app/src/main/java/a/Pool.java": _POOL.format(package="a"),
        "app/src/main/java/a/Svc.java": (
            "package a;\n"
            "import java.util.concurrent.*;\n"
            "class Svc {\n"
            "  private ExecutorService pool;\n"
            "  void stop() { pool.shutdown(); }\n"
            "}\n"
        ),
    }, [])

    # ADR-71: a library can supply the pool, so one source body proves nothing.
    assert _external_calls(symbols, "a.Svc.") == [("a.Pool.shutdown", "possible")]


def test_object_methods_reach_the_body_a_source_type_declares(tmp_path: Path) -> None:
    symbols = _symbols(tmp_path, {
        "app/src/main/java/p/Ids.java": (
            "package p;\n"
            "import lombok.Data;\n"
            "class TripId { public String toString() { return \"\"; } }\n"
            "class Base { public int hashCode() { return 1; } }\n"
            "class Child extends Base {}\n"
            "@Data class Trip { private TripId tripId; private static TripId shared; }\n"
            "class Svc {\n"
            "  void run(Trip trip, Child child, Object any) {\n"
            "    trip.getTripId().toString(); child.hashCode(); any.hashCode(); trip.getShared();\n"
            "  }\n"
            "}\n"
        ),
    })

    calls = sorted(
        (call.callee, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller == "p.Svc.run"
    )

    # A Lombok ``@Data`` getter types the chained receiver; a source
    # ancestor's body is reached; an ``Object`` receiver dispatches nowhere.
    assert calls == [("p.Base.hashCode", "exact"), ("p.TripId.toString", "exact")]


def test_unqualified_calls_bind_to_the_innermost_type_that_declares_the_name(
    tmp_path: Path,
) -> None:
    symbols = _symbols(tmp_path, {
        "app/src/main/java/p/Outer.java": (
            "package p;\n"
            "import java.util.concurrent.*;\n"
            "import org.example.Provider;\n"
            "interface Pattern { String skip(); }\n"
            "class Outer {\n"
            "  private static String helper(String value) { return value; }\n"
            "  private String fetch() { return \"\"; }\n"
            "  class Task implements Callable<String> {\n"
            "    public String call() { return fetch(); }\n"
            "  }\n"
            "  static class Config {\n"
            "    String build() { return helper(\"x\"); }\n"
            "    Pattern pattern() { return new Pattern() { public String skip() { return helper(\"y\"); } }; }\n"
            "    Provider external() { return new Provider() { public String get() { return helper(\"z\"); } }; }\n"
            "  }\n"
            "}\n"
        ),
    })

    calls = sorted(
        (call.caller.rsplit(".", 2)[-2:][0] + "." + call.caller.rsplit(".", 1)[-1], call.callee, call.call_line)
        for call in symbols.fallback_calls(set(), closed_world=True)
    )

    # An inner class reaches its outer type's methods, also from an
    # anonymous body whose source supertype lacks the name. An anonymous
    # class of an external supertype may inherit ``helper``: unresolved.
    assert calls == [
        ("Config.build", "p.Outer.helper", 12),
        ("Config.pattern", "p.Outer.helper", 13),
        ("Task.call", "p.Outer.fetch", 9),
    ]


def test_a_provably_incompatible_argument_separates_same_arity_overloads(tmp_path: Path) -> None:
    symbols = _symbols(tmp_path, {
        "app/src/main/java/p/Tags.java": (
            "package p;\n"
            "import java.util.Map;\n"
            "class Span {}\n"
            "class Tags {\n"
            "  void add(Span span, String url, String host) {}\n"
            "  void add(String url, String host, Map<String, String> headers) {}\n"
            "  void put(String key, Object value) {}\n"
            "  void put(Object key, String value) {}\n"
            "}\n"
            "class Svc {\n"
            "  private Tags tags;\n"
            "  void run(Span span, Object uri, Object request, Object other) {\n"
            "    tags.add(span, request.toString(), other.getHost());\n"
            "    tags.add(uri.toString(), other.getHost(), request.getHeaders());\n"
            "    tags.put(other.name(), other.value());\n"
            "    Span[] spans = new Span[] { span }; spans[0].toString();\n"
            "  }\n"
            "}\n"
        ),
    })

    calls = sorted(
        (call.callee_line, call.call_line, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller == "p.Svc.run"
    )

    # ``span`` cannot be a ``String`` and ``toString()`` returns one; the
    # unknown arguments of ``put`` select nothing and it stays unresolved.
    assert calls == [(5, 13, "exact"), (6, 14, "exact")]


def test_a_test_source_set_never_reaches_the_call_graph(tmp_path: Path) -> None:
    """Review S14: a type declared in ``src/main`` and ``src/test`` of one module.

    The file inventory excludes the test source set, so the indexed call
    graph binds production code to the ``src/main`` declaration exactly.
    """
    from systemlens.indexing.service import index_repo
    from systemlens.infrastructure.config import Config
    from systemlens.storage.sqlite import Store

    repo = tmp_path / "repo"
    (repo / "src/main/java/p").mkdir(parents=True)
    (repo / "src/test/java/p").mkdir(parents=True)
    (repo / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion><groupId>p</groupId>"
        "<artifactId>clock</artifactId><version>1.0</version></project>",
        encoding="utf-8",
    )
    (repo / "src/main/java/p/Use.java").write_text(
        "package p;\npublic class Use {\n  Clock clock;\n  void run() { clock.now(); }\n}\n",
        encoding="utf-8",
    )
    clock = "package p;\npublic class Clock {\n  public long now() { return %d; }\n}\n"
    (repo / "src/main/java/p/Clock.java").write_text(clock % 0, encoding="utf-8")
    (repo / "src/test/java/p/Clock.java").write_text(clock % 1, encoding="utf-8")

    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="source"), store, full=True)
        methods = {method.id: method for method in store.all_integration_methods()}
        edges = [
            (methods[edge.caller_id].qualified_method, methods[edge.callee_id].path, edge.dispatch_confidence)
            for edge in store.all_call_edges()
            if edge.caller_id in methods and edge.callee_id in methods
        ]

    assert not any("src/test/" in method.path for method in methods.values())
    assert ("p.Use.run", "src/main/java/p/Clock.java", "exact") in edges


class _NoScan(dict):  # type: ignore[type-arg]
    """A mapping that fails when a call site iterates it instead of looking up."""

    def __iter__(self):  # type: ignore[no-untyped-def]
        raise AssertionError("a call site scanned every declaration")

    def items(self):  # type: ignore[no-untyped-def]
        raise AssertionError("a call site scanned every declaration")

    def keys(self):  # type: ignore[no-untyped-def]
        raise AssertionError("a call site scanned every declaration")


def test_call_sites_look_declarations_up_instead_of_scanning_them(tmp_path: Path) -> None:
    """Review S13: a receiver without a source contract scanned every method.

    A Spring Data call (``repo.save``) and an unqualified helper call read
    the declarations of their receiver's types through indexes, so the
    symbol pass stays linear in the call sites.
    """
    symbols = _symbols(tmp_path, {"app/src/main/java/p/Svc.java": """package p;
import org.springframework.data.jpa.repository.JpaRepository;
interface Repo extends JpaRepository<Object, Long> {
  Object findByName(String name);
}
class Svc {
  Repo repo;
  void run() {
    repo.save(null);
    repo.findByName("x");
    helper();
  }
  void helper() {}
}
"""})
    symbols.by_owner = _NoScan(symbols.by_owner)
    calls = {
        (call.callee.rsplit(".", 1)[-1], call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
    }
    assert calls == {("findByName", "possible"), ("helper", "exact")}


_UNTYPED_LAMBDAS = """package p;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;
class Employee { void hire() {} void fire() {} }
class Department {
  Long getId() { return 1L; }
  Long getOrganizationId() { return 1L; }
  void setEmployees(List<Employee> employees) {}
}
class Repo {
  private List<Department> departments = new ArrayList<>();
  Department findById(Long id) {
    return departments.stream()
        .filter(a -> a.getId().equals(id))
        .findFirst()
        .orElseThrow();
  }
  List<Department> findByOrganization(Long organizationId) {
    return departments.stream()
        .filter(a -> a.getOrganizationId().equals(organizationId))
        .toList();
  }
  void withEmployees(Long organizationId) {
    List<Department> found = findByOrganization(organizationId);
    found.forEach(d -> d.setEmployees(null));
  }
  void shapes(Map<Long, Employee> staff, Optional<Employee> maybe, Stream<? extends Employee> all) {
    staff.forEach((id, e) -> e.hire());
    maybe.ifPresent(e -> e.fire());
    all.sorted((left, right) -> { left.hire(); return 0; });
  }
  void unproven(List<?> anything, List raw, Map<Long, Department> byId) {
    anything.forEach(x -> x.getId());
    raw.forEach(x -> x.getId());
    byId.computeIfAbsent(1L, key -> key.getId());
    apply(x -> x.getId());
  }
  void apply(java.util.function.Function<?, Long> f) {}
}
"""


def test_an_untyped_lambda_parameter_takes_a_provable_element_type(tmp_path: Path) -> None:
    """BL-14: a lambda passed to a JDK collection, stream, optional or map
    callback takes its parameter type from the receiver's written type
    arguments; any other target leaves the parameter unresolved."""
    symbols = _symbols(tmp_path, {"app/src/main/java/p/Repo.java": _UNTYPED_LAMBDAS})

    def line(text: str) -> int:
        return next(
            number for number, value in enumerate(_UNTYPED_LAMBDAS.splitlines(), 1) if text in value
        )

    calls = sorted(
        (call.caller.rsplit(".", 1)[-1], call.callee.removeprefix("p."), call.call_line)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller.startswith("p.Repo.")
    )
    assert calls == [
        ("findById", "Department.getId", line("a.getId()")),
        ("findByOrganization", "Department.getOrganizationId", line("a.getOrganizationId()")),
        ("shapes", "Employee.fire", line("e.fire()")),
        ("shapes", "Employee.hire", line("e.hire()")),
        ("shapes", "Employee.hire", line("left.hire()")),
        ("unproven", "Repo.apply", line("apply(x ->")),
        ("withEmployees", "Department.setEmployees", line("d.setEmployees")),
        ("withEmployees", "Repo.findByOrganization", line("findByOrganization(organizationId);")),
    ]
