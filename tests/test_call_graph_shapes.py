"""Call shapes of the ``source`` engine: one fixture per row of the shape list.

Each case is a row of section 2 of ``docs/plans/call-graph-coverage.md``. A
fixture tags the lines it refers to with a trailing ``//@Tag`` comment: the
caller's declaration line and the declaration line of every target. The
symbol pass runs in a closed world, as the ``source`` engine does, and the
case compares the targets reached from the caller with their confidence.

A row whose shape is not delivered is a strict expected failure that names
the task owning it. When a decision record has yet to fix a confidence or a
name, the case states only what any delivery must show: ``expected`` with a
``None`` confidence, and ``absent`` targets.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
import textwrap

import pytest

from systemlens.domain.module_inventory import DiscoveredModule, ModuleDependency
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing.java_symbols import JavaSymbols

_APP = "app/src/main/java/p/"
_TAG = re.compile(r"//@([\w.]+)\s*$")


@dataclass(frozen=True)
class Shape:
    row: str
    source: str | dict[str, str]
    caller: str
    expected: dict[str, str | None]
    # Every reached target is listed unless ``partial``: then other targets
    # may appear, but ``absent`` ones may not.
    partial: bool = False
    absent: tuple[str, ...] = ()
    deps: tuple[tuple[str, str], ...] = ()
    pending: str | None = None
    note: str = field(default="", compare=False)


def _files(shape: Shape) -> dict[str, str]:
    if isinstance(shape.source, str):
        return {f"{_APP}A.java": "package p;\n" + textwrap.dedent(shape.source).lstrip("\n")}
    return {path: textwrap.dedent(text).lstrip("\n") for path, text in shape.source.items()}


def _reached(tmp_path: Path, shape: Shape) -> tuple[dict[str, set[str]], set[str]]:
    """Targets reached from the caller: tag -> confidences, and every tag."""
    files = _files(shape)
    tags: dict[tuple[str, int], str] = {}
    lines: dict[str, tuple[str, int]] = {}
    modules: dict[str, DiscoveredModule] = {}
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            match = _TAG.search(line)
            if match:
                assert match.group(1) not in lines, f"duplicate tag {match.group(1)}"
                tags[(path, number)] = match.group(1)
                lines[match.group(1)] = (path, number)
        name = path.split("/", 1)[0]
        modules[name] = DiscoveredModule(
            name=name, path=tmp_path / name, build_system="maven", version=None,
            kind="application", starts_application=True, configuration_example="",
        )
    methods = materialize_integration_methods(tmp_path, [], list(files), list(modules.values()))
    symbols = JavaSymbols(
        tmp_path, methods, list(files),
        module_dependencies=[ModuleDependency(source=a, target=b) for a, b in shape.deps],
    )
    caller = lines[shape.caller]
    reached: dict[str, set[str]] = {}
    for call in symbols.fallback_calls(set(), closed_world=True):
        if (call.caller_path, call.caller_line) != caller:
            continue
        label = tags.get((call.callee_path, call.callee_line), f"{call.callee}@{call.callee_line}")
        reached.setdefault(label, set()).add(call.dispatch_confidence)
    return reached, set(lines)


SHAPES: list[Shape] = [
    # 2.1 Static binding
    Shape("S1", """
        class Service {
          void a() {} //@Service.a
          Service self() { return this; } //@Service.self
          void b() {} //@Service.b
        }
        class Use {
          Service field;
          void run(Service param) { //@Use.run
            field.a();
            Service local = new Service();
            local.a();
            param.a();
            var v = new Service();
            v.a();
            field.self().b();
          }
        }
        """, "Use.run",
        {"Service.a": "exact", "Service.self": "exact", "Service.b": "exact"}),
    Shape("S2", """
        class Base {
          static void util() {} //@Base.util
        }
        class Sub extends Base {
          static void util() {} //@Sub.util
        }
        class Use {
          void run() { //@Use.run
            Base.util();
          }
        }
        """, "Use.run", {"Base.util": "exact"}),
    Shape("S2-subclass", """
        class Base {
          static void util() {} //@Base.util
        }
        class Sub extends Base {
          static void util() {} //@Sub.util
        }
        class Use {
          void run() { //@Use.run
            Sub.util();
          }
        }
        """, "Use.run", {"Sub.util": "exact"}),
    Shape("S2-import", {
        f"{_APP}l/Util.java": """
            package p.l;
            public class Util {
              public static void help() {} //@Util.help
            }
            """,
        f"{_APP}a/Use.java": """
            package p.a;
            import static p.l.Util.help;
            class Use {
              void run() { //@Use.run
                help();
              }
            }
            """,
    }, "Use.run", {"Util.help": "exact"}),
    Shape("S3", """
        class Outer {
          void helper() {} //@Outer.helper
          class Inner {
            void run() { //@Inner.run
              helper();
            }
          }
        }
        """, "Inner.run", {"Outer.helper": "exact"}),
    Shape("S4", """
        class Base {
          private void go() {} //@Base.go
          void run() { //@Base.run
            go();
          }
        }
        class Sub extends Base {
          private void go() {} //@Sub.go
        }
        """, "Base.run", {"Base.go": "exact"}),
    Shape("S4-public-homonym", """
        class Base {
          private void go() {} //@Base.go
          void run() { //@Base.run
            go();
          }
        }
        class Sub extends Base {
          public void go() {} //@Sub.go
        }
        """, "Base.run", {"Base.go": "exact"}),
    Shape("S5", """
        class Base {}
        class Sub extends Base {}
        class Svc {
          void f(Base b) {} //@Svc.f.Base
          void f(String s) {} //@Svc.f.String
        }
        class Use {
          Svc s;
          void run(Sub x) { //@Use.run
            s.f(new Sub());
            s.f(x);
          }
        }
        """, "Use.run", {"Svc.f.Base": "exact"}),
    Shape("S6", """
        class Base {}
        class Sub extends Base {}
        class Svc {
          void f(Base b) {} void f(String s) {} //@Svc.f
        }
        class Use {
          Svc s;
          void run() { //@Use.run
            s.f(new Sub());
          }
        }
        """, "Use.run", {"Svc.f": "exact"},
        note="both overloads share a line; the call graph locates a target by line"),
    Shape("S6-anonymous", """
        class Task {
          void run() { Runnable r = new Runnable() { public void run() {} }; } //@Task.run
        }
        class Use {
          Task task;
          void go() { //@Use.go
            task.run();
          }
        }
        """, "Use.go", {"Task.run": "exact"}),
    Shape("S7", """
        class Service {
          Service() {} //@Service.new
        }
        class Use {
          void run() { //@Use.run
            new Service();
          }
        }
        """, "Use.run", {"Service.new": None}, partial=True,
        pending="K7: constructors are not methods of the graph"),
    Shape("S8", """
        class Service {
          void init() {} //@Service.init
        }
        class Use {
          Service s = new Service();
          Use() { //@Use.new
            s.init();
          }
        }
        """, "Use.new", {"Service.init": None}, partial=True,
        pending="K7: constructor and initializer bodies are never callers"),
    # 2.2 Inheritance and dispatch
    Shape("H1", """
        class Base {
          void inherited() {} //@Base.inherited
        }
        class Sub extends Base {}
        class Use {
          Sub sub;
          void run() { //@Use.run
            sub.inherited();
          }
        }
        """, "Use.run", {"Base.inherited": "exact"}),
    Shape("H2-declared", """
        abstract class Base {
          protected void hook() {} //@Base.hook
        }
        class Sub extends Base {
          protected void hook() {} //@Sub.hook
        }
        class Use {
          Sub sub;
          void run() { //@Use.run
            sub.hook();
          }
        }
        """, "Use.run", {"Sub.hook": "exact"}),
    Shape("H2-virtual", """
        class A {
          void m() {} //@A.m
        }
        class B extends A {}
        class C extends B {
          void m() {} //@C.m
        }
        class Use {
          A a;
          void run() { //@Use.run
            a.m();
          }
        }
        """, "Use.run", {"A.m": "possible", "C.m": "possible"}),
    Shape("H3", """
        abstract class Base {
          void template() { //@Base.template
            this.step();
            step();
          }
          abstract void step(); //@Base.step
        }
        class SubA extends Base {
          void step() {} //@SubA.step
        }
        class SubB extends Base {
          void step() {} //@SubB.step
        }
        """, "Base.template", {"SubA.step": "possible", "SubB.step": "possible"}),
    Shape("H4", """
        class Base {
          void foo() {} //@Base.foo
        }
        class Sub extends Base {
          void foo() { //@Sub.foo
            super.foo();
          }
        }
        """, "Sub.foo", {"Base.foo": "exact"}),
    Shape("H5", """
        class GrandBase {
          void foo() {} //@GrandBase.foo
        }
        class Base extends GrandBase {}
        class Sub extends Base {
          void foo() { //@Sub.foo
            super.foo();
          }
        }
        """, "Sub.foo", {"GrandBase.foo": "exact"}),
    Shape("H6", """
        class Base {
          void foo(String s) {} //@Base.foo.String
          void foo(Integer i) {} //@Base.foo.Integer
        }
        class Sub extends Base {
          void foo(String s) { //@Sub.foo
            super.foo(s);
          }
        }
        """, "Sub.foo", {"Base.foo.String": "exact"}),
    Shape("H7-abstract-intermediate", """
        interface Port {
          void send(); //@Port.send
        }
        abstract class AbstractImpl implements Port {}
        class Impl extends AbstractImpl {
          public void send() {} //@Impl.send
        }
        class Use {
          Port p;
          void run() { //@Use.run
            p.send();
          }
        }
        """, "Use.run", {"Impl.send": "exact", "Port.send": "possible"}),
    Shape("H7-inherited-body", """
        interface Port {
          void send(); //@Port.send
        }
        class Base {
          public void send() {} //@Base.send
        }
        class Impl extends Base implements Port {}
        class Use {
          Port p;
          void run() { //@Use.run
            p.send();
          }
        }
        """, "Use.run", {"Base.send": "exact", "Port.send": "possible"}),
    Shape("H7-default", """
        interface Port {
          default void ping() {} //@Port.ping
        }
        class Impl implements Port {}
        class Use {
          Port p;
          void run() { //@Use.run
            p.ping();
          }
        }
        """, "Use.run", {"Port.ping": "exact"}),
    Shape("H7-static", """
        interface Port {
          static Port create() { return null; } //@Port.create
        }
        class Use {
          void run() { //@Use.run
            Port.create();
          }
        }
        """, "Use.run", {"Port.create": "exact"}),
    Shape("H12", """
        interface Port {
          default void ping() {} //@Port.ping
        }
        class Impl implements Port {
          public void ping() { //@Impl.ping
            Port.super.ping();
          }
        }
        """, "Impl.ping", {"Port.ping": "exact"}),
    Shape("H12-outer", """
        class Base {
          void foo() {} //@Base.foo
        }
        class Sub extends Base {
          void foo() {} //@Sub.foo
          class Inner {
            void run() { //@Inner.run
              Sub.super.foo();
            }
          }
        }
        """, "Inner.run", {"Base.foo": "exact"}),
    Shape("H8", """
        interface Named {
          String name(); //@Named.name
        }
        record R(String n) implements Named {
          public String name() { return n; } //@R.name
        }
        class Use {
          Named x;
          void run() { //@Use.run
            x.name();
          }
        }
        """, "Use.run", {"R.name": "exact", "Named.name": "possible"}),
    Shape("H9", """
        enum Op {
          A {
            void run() {} //@Op.A.run
          },
          B {
            void run() {} //@Op.B.run
          };
          abstract void run(); //@Op.run
        }
        class Use {
          Op op;
          void exec() { //@Use.exec
            op.run();
          }
        }
        """, "Use.exec", {"Op.A.run": "possible", "Op.B.run": "possible"},
        note="each constant body is a method of the enum type, on its own line"),
    Shape("H10", """
        import com.example.ext.Listener;
        class Impl implements Listener {
          public void onEvent() {} //@Impl.onEvent
        }
        class Use {
          Listener l;
          void run() { //@Use.run
            l.onEvent();
          }
        }
        """, "Use.run", {"Impl.onEvent": "possible"}),
    Shape("H11", """
        class MyRunner extends Thread {
          public void run() {} //@MyRunner.run
        }
        class Use {
          Thread t;
          void go() { //@Use.go
            t.run();
          }
        }
        """, "Use.go", {"MyRunner.run": "possible"},
        pending="K6: no dispatch from an external class to a source override"),
    # 2.3 Generics
    Shape("G1", """
        class Order {}
        interface Handler<T> {
          void handle(T t); //@Handler.handle
        }
        class OrderHandler implements Handler<Order> {
          public void handle(Order o) {} //@OrderHandler.handle
        }
        class Use {
          Handler<Order> h;
          void run(Order o) { //@Use.run
            h.handle(o);
          }
        }
        """, "Use.run", {"OrderHandler.handle": "exact", "Handler.handle": "possible"}),
    Shape("G2", """
        class Base<T> {
          void save(T t) {} //@Base.save
        }
        class Repo extends Base<String> {}
        class StringRepo extends Base<String> {
          void save(String s) {} //@StringRepo.save
        }
        class Use {
          Repo r;
          void run() { //@Use.run
            r.save("x");
          }
        }
        """, "Use.run", {"Base.save": "exact"}),
    Shape("G2-override", """
        class Base<T> {
          void save(T t) {} //@Base.save
        }
        class StringRepo extends Base<String> {
          void save(String s) {} //@StringRepo.save
        }
        class Use {
          Base<String> b;
          void run() { //@Use.run
            b.save("x");
          }
        }
        """, "Use.run", {"Base.save": "possible", "StringRepo.save": "possible"}),
    Shape("G3", """
        interface Handler<T> {
          void handle(T t); //@Handler.handle
        }
        class Generic<T> implements Handler<T> {
          public void handle(T t) {} //@Generic.handle
        }
        class Use {
          Handler<String> h;
          void run() { //@Use.run
            h.handle("x");
          }
        }
        """, "Use.run", {"Generic.handle": "exact", "Handler.handle": "possible"},
        pending="K5: a body whose parameter is a type variable is not indexed for dispatch"),
    Shape("G4", """
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        class Use {
          <T extends Shape> void run(T t) { //@Use.run
            t.area();
          }
        }
        """, "Use.run", {"Circle.area": "possible", "Square.area": "possible"},
        pending="K5: a receiver typed by a type variable is not typed by its bound"),
    Shape("G4-field", """
        class Repo {
          void save() {} //@Repo.save
        }
        class Svc<R extends Repo> {
          R repo;
          void run() { //@Svc.run
            repo.save();
          }
        }
        """, "Svc.run", {"Repo.save": "exact"},
        pending="K5: a field typed by a type variable is not typed by its bound"),
    # 2.4 Receiver typing
    Shape("T1", """
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        class Use {
          void run(Shape s, Circle[] all) { //@Use.run
            ((Circle) s).area();
            all[0].area();
          }
        }
        """, "Use.run", {"Circle.area": "exact"}),
    Shape("T2", """
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        class Use {
          void run(Shape s) { //@Use.run
            if (s instanceof Circle c) {
              c.area();
            }
          }
        }
        """, "Use.run", {"Circle.area": "exact"},
        pending="K5: the variable of an instanceof pattern has no type"),
    Shape("T3", """
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        class Use {
          double run(Shape s) { //@Use.run
            return switch (s) {
              case Circle c -> c.area();
              default -> 0;
            };
          }
        }
        """, "Use.run", {"Circle.area": "exact"},
        pending="K5: the variable of a switch pattern has no type"),
    # 2.5 Functional and implicit implementations
    Shape("F1", """
        interface Port {
          void send(); //@Port.send
        }
        class A1 implements Port {
          public void send() {} //@A1.send
        }
        class A2 implements Port {
          public void send() {} //@A2.send
        }
        class Use {
          java.util.List<Port> ports;
          Port port;
          void run() { //@Use.run
            ports.forEach(Port::send);
            ports.forEach(p -> port.send());
          }
        }
        """, "Use.run", {"A1.send": "possible", "A2.send": "possible"}),
    Shape("F2", """
        interface Fn {
          void apply(); //@Fn.apply
        }
        class FnImpl implements Fn {
          public void apply() {} //@FnImpl.apply
        }
        class Use {
          Fn f;
          void run() { //@Use.run
            Fn g = () -> {};
            f.apply();
          }
        }
        """, "Use.run", {"FnImpl.apply": "possible"}, partial=True,
        pending="K3: a lambda implementation keeps the named body exact"),
    Shape("F3", """
        class Base {
          void hook() {} //@Base.hook
        }
        class Use {
          void run() { //@Use.run
            Base b = new Base() { void hook() {} };
            b.hook();
          }
        }
        """, "Use.run", {"Base.hook": "possible"}, partial=True,
        pending="K3: an anonymous subclass keeps the base body exact"),
    Shape("F4", """
        class Task implements Runnable {
          public void run() {} //@Task.run
        }
        class Use {
          Runnable r;
          void go() { //@Use.go
            r.run();
          }
        }
        """, "Use.go", {"Task.run": "possible"}),
    # 2.6 Module boundaries
    Shape("M1", {
        "lib/src/main/java/l/Local.java": """
            package l;
            import com.example.ext.Listener;
            public class Local implements Listener {
              public void onEvent() {} //@Local.onEvent
            }
            """,
        "lib/src/main/java/l/Use.java": """
            package l;
            import com.example.ext.Listener;
            public class Use {
              Listener l;
              void run() { //@Use.run
                l.onEvent();
              }
            }
            """,
        "app/src/main/java/a/Remote.java": """
            package a;
            import com.example.ext.Listener;
            public class Remote implements Listener {
              public void onEvent() {} //@Remote.onEvent
            }
            """,
    }, "Use.run", {"Local.onEvent": "possible"}, deps=(("app", "lib"),)),
    Shape("M2", {
        "lib/src/main/java/l/Base.java": """
            package l;
            public class Base {
              public void foo() {} //@Base.foo
            }
            """,
        "lib/src/main/java/l/Use.java": """
            package l;
            public class Use {
              Base b;
              void run() { //@Use.run
                b.foo();
              }
            }
            """,
        "app/src/main/java/a/Sub.java": """
            package a;
            import l.Base;
            public class Sub extends Base {
              public void foo() {} //@Sub.foo
            }
            """,
    }, "Use.run", {"Base.foo": None}, partial=True, absent=("Sub.foo",),
        deps=(("app", "lib"),),
        pending="K4: source-type dispatch leaves the caller's visible modules"),
    Shape("M3", {
        "lib1/src/main/java/l/Base.java": """
            package l;
            public class Base {
              public void go() {} //@lib1.Base.go
            }
            """,
        "lib2/src/main/java/l/Base.java": """
            package l;
            public class Base {
              public void go() {} //@lib2.Base.go
              public void other() {}
            }
            """,
        "app/src/main/java/a/Use.java": """
            package a;
            import l.Base;
            class Use {
              Base b;
              void run() { //@Use.run
                b.go();
              }
            }
            """,
    }, "Use.run", {"lib1.Base.go": "exact"}, deps=(("app", "lib1"),),
        pending="K4: dependencies do not choose between two declarations of one type"),
    # 2.7 Framework-mediated calls (Spring)
    Shape("W1", """
        import org.springframework.beans.factory.annotation.Autowired;
        interface Repo {
          void save(); //@Repo.save
        }
        class RepoImpl implements Repo {
          public void save() {} //@RepoImpl.save
        }
        class Svc {
          @Autowired Repo repo;
          void run() { //@Svc.run
            repo.save();
          }
        }
        """, "Svc.run", {"RepoImpl.save": "exact", "Repo.save": "possible"}),
    Shape("W2", """
        import org.springframework.beans.factory.annotation.Autowired;
        import org.springframework.beans.factory.annotation.Qualifier;
        import org.springframework.stereotype.Component;
        interface Port {
          void send(); //@Port.send
        }
        @Component("first")
        class First implements Port {
          public void send() {} //@First.send
        }
        @Component("second")
        class Second implements Port {
          public void send() {} //@Second.send
        }
        class Use {
          @Autowired @Qualifier("first") Port port;
          void run() { //@Use.run
            port.send();
          }
        }
        """, "Use.run", {"First.send": None}, partial=True, absent=("Second.send",),
        pending="K8: @Qualifier does not narrow the candidates"),
    Shape("W3", """
        import org.springframework.context.ApplicationEventPublisher;
        import org.springframework.context.event.EventListener;
        class OrderPlaced {}
        class Listener {
          @EventListener void on(OrderPlaced event) {} //@Listener.on
        }
        class Use {
          ApplicationEventPublisher publisher;
          void run() { //@Use.run
            publisher.publishEvent(new OrderPlaced());
          }
        }
        """, "Use.run", {"Listener.on": None}, partial=True,
        pending="K8: publishEvent reaches no @EventListener"),
    Shape("W4", """
        import org.springframework.scheduling.annotation.Async;
        class Mailer {
          @Async public void send() {} //@Mailer.send
        }
        class Use {
          Mailer mailer;
          void run() { //@Use.run
            mailer.send();
          }
        }
        """, "Use.run", {"Mailer.send": "exact"},
        note="a plain call: no asynchronous marker"),
    Shape("W5", """
        import org.springframework.scheduling.annotation.Scheduled;
        class Service {
          void tick() {} //@Service.tick
        }
        class Job {
          Service service;
          @Scheduled(fixedDelay = 1000) void every() { //@Job.every
            service.tick();
          }
        }
        """, "Job.every", {"Service.tick": "exact"},
        note="the transient trigger itself is covered by the scheduled-trigger tests"),
    Shape("W6", """
        import jakarta.annotation.PostConstruct;
        import org.springframework.boot.CommandLineRunner;
        class Service {
          void warm() {} //@Service.warm
        }
        class Boot implements CommandLineRunner {
          Service service;
          public void run(String... args) { //@Boot.run
            service.warm();
          }
          @PostConstruct
          void init() {
            service.warm();
          }
        }
        """, "Boot.run", {"Service.warm": "exact"},
        note="the calls are edges; making these methods flow roots is K8"),
    Shape("W7", """
        import org.springframework.data.jpa.repository.JpaRepository;
        class Order {}
        interface OrderRepo extends JpaRepository<Order, Long> {
          Order findByOrderId(String id); //@OrderRepo.findByOrderId
        }
        class Svc {
          OrderRepo repo;
          void run() { //@Svc.run
            repo.findByOrderId("x");
          }
        }
        """, "Svc.run", {"OrderRepo.findByOrderId": "possible"}),
    Shape("W8", """
        import org.mapstruct.Mapper;
        class Order {}
        class OrderDto {}
        @Mapper
        interface OrderMapper {
          OrderDto toDto(Order order); //@OrderMapper.toDto
        }
        class Svc {
          OrderMapper mapper;
          void run(Order order) { //@Svc.run
            mapper.toDto(order);
          }
        }
        """, "Svc.run", {"OrderMapper.toDto": "possible"},
        note="the generated body is not in the sources: the declaration is the target"),
]


def _param(shape: Shape) -> object:
    marks = [pytest.mark.xfail(strict=True, reason=shape.pending)] if shape.pending else []
    return pytest.param(shape, id=shape.row, marks=marks)


@pytest.mark.parametrize("shape", [_param(shape) for shape in SHAPES])
def test_call_shape(tmp_path: Path, shape: Shape) -> None:
    reached, tags = _reached(tmp_path, shape)
    for tag in [*shape.expected, *shape.absent]:
        assert tag in tags, f"{shape.row}: no //@{tag} line in the fixture"
    got = {
        tag: confidences for tag, confidences in reached.items()
        if not shape.partial or tag in shape.expected or tag in shape.absent
    }
    for tag, confidence in shape.expected.items():
        assert tag in got, f"{shape.row}: {tag} not reached; reached {reached}"
        if confidence is not None:
            assert got[tag] == {confidence}, f"{shape.row}: {tag} is {got[tag]}; reached {reached}"
    for tag in shape.absent:
        assert tag not in got, f"{shape.row}: {tag} must not be reached; reached {reached}"
    if not shape.partial:
        assert set(got) == set(shape.expected), f"{shape.row}: reached {reached}"


def test_every_shape_row_has_a_case() -> None:
    plan = Path(__file__).resolve().parents[1] / "docs/plans/call-graph-coverage.md"
    rows = set(re.findall(r"^\| ([SHGTFMW]\d+) \|", plan.read_text(encoding="utf-8"), re.MULTILINE))
    cases = {shape.row.split("-", 1)[0] for shape in SHAPES}
    assert rows == cases, f"rows without a case: {sorted(rows - cases)}; cases without a row: {sorted(cases - rows)}"
