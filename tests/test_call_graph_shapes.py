"""Call shapes of the ``source`` engine: one fixture per row of the shape list.

Each case is a row of the call-shape table of ``docs/spec-tech/indexing.md``. A
fixture tags the lines it refers to with a trailing ``//@Tag`` comment: the
caller's declaration line and the declaration line of every target. The
symbol pass runs in a closed world, as the ``source`` engine does, and the
case compares the targets reached from the caller with their confidence.

A row whose shape is not delivered is a strict expected failure that names
the backlog item owning it. When a decision record has yet to fix a
confidence or a name, the case states only what any delivery must show: ``expected`` with a
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
    Shape("S3-anonymous-user-details", """
        import org.springframework.security.core.userdetails.UserDetails;
        class Jwt {
          static String name(String token) { //@Jwt.name
            return token;
          }
          static Object authenticate(String token) { //@Jwt.authenticate
            UserDetails details = new UserDetails() {
              public String getUsername() {
                return name(token);
              }
            };
            return details;
          }
        }
        """, "Jwt.authenticate", {"Jwt.name": "exact"},
        note="review D6: UserDetails declares fixed members, so name() is the outer method"),
    Shape("S3-anonymous-thread", """
        class Use {
          void helper() {} //@Use.helper
          void start() { //@Use.start
            Thread t = new Thread() {
              public void run() {
                helper();
              }
            };
            t.start();
          }
        }
        """, "Use.start", {"Use.helper": "exact"},
        note="BL-25: Thread's members are fixed, so helper() is the outer method"),
    Shape("S3-anonymous-thread-member", """
        class Use {
          void interrupt() {} //@Use.interrupt
          void start() { //@Use.start
            Thread t = new Thread() {
              public void run() {
                interrupt();
              }
            };
            t.start();
          }
        }
        """, "Use.start", {}, absent=("Use.interrupt",),
        note="BL-25: Thread declares interrupt(), so the call stays unresolved"),
    Shape("S3-anonymous-timer-task", """
        import java.util.TimerTask;
        class Use {
          void helper() {} //@Use.helper
          void cancel() {} //@Use.cancel
          TimerTask task() { //@Use.task
            return new TimerTask() {
              public void run() {
                helper();
                cancel();
              }
            };
          }
        }
        """, "Use.task", {"Use.helper": "exact"}, absent=("Use.cancel",),
        note="BL-25: TimerTask declares cancel(), so that call stays unresolved"),
    Shape("S3-anonymous-object", """
        class Use {
          void helper() {} //@Use.helper
          Object make() { //@Use.make
            return new Object() {
              void go() {
                helper();
              }
            };
          }
        }
        """, "Use.make", {"Use.helper": "exact"}),
    Shape("S3-anonymous-spring-adapters", """
        import org.springframework.web.servlet.config.annotation.WebMvcConfigurerAdapter;
        import org.springframework.security.config.annotation.web.configuration.WebSecurityConfigurerAdapter;
        class Use {
          void helper() {} //@Use.helper
          void init() {} //@Use.init
          void build() { //@Use.build
            Object mvc = new WebMvcConfigurerAdapter() {
              void go() {
                helper();
              }
            };
            Object security = new WebSecurityConfigurerAdapter() {
              void go() {
                helper();
                init();
              }
            };
          }
        }
        """, "Use.build", {"Use.helper": "exact"}, absent=("Use.init",),
        note="BL-25: init() is a WebSecurityConfigurerAdapter member and stays unresolved"),
    Shape("S3-anonymous-unknown-external", """
        import com.example.lib.Task;
        class Use {
          void helper() {} //@Use.helper
          void build() { //@Use.build
            Task task = new Task() {
              public void run() {
                helper();
              }
            };
          }
        }
        """, "Use.build", {}, absent=("Use.helper",),
        note="BL-25: an external supertype outside the lists may declare helper()"),
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
    Shape("S5-widening-before-boxing", """
        class Svc {
          void m(long x) {} //@Svc.m.long
          void m(Integer x) {} //@Svc.m.Integer
          void n(Object x) {} //@Svc.n.Object
          void n(int x) {} //@Svc.n.int
        }
        class Use {
          Svc svc;
          void run() { //@Use.run
            svc.m(1);
            Integer boxed = 1;
            svc.n(boxed);
          }
        }
        """, "Use.run", {"Svc.m.long": "exact", "Svc.n.Object": "exact"},
        note="review S3: widening and subtyping win over boxing and unboxing"),
    Shape("S5-literal-suffix", """
        class Svc {
          void k(int x) {} //@Svc.k.int
          void k(long x) {} //@Svc.k.long
          void f(float x) {} //@Svc.f.float
          void f(double x) {} //@Svc.f.double
        }
        class Use {
          Svc svc;
          void run() { //@Use.run
            svc.k(10L);
            svc.k(0x1FL);
            svc.f(1.5f);
            svc.f(-2.5F);
          }
        }
        """, "Use.run", {"Svc.k.long": "exact", "Svc.f.float": "exact"},
        note="review S4: a literal's suffix gives its type"),
    Shape("S5-unknown-stays-open", """
        import java.util.ArrayList;
        import java.util.Collection;
        class Svc {
          void m(Object x) {} //@Svc.m.Object
          void m(Collection x) {} //@Svc.m.Collection
        }
        class Use {
          Svc svc;
          void run(ArrayList items, Unknown u) { //@Use.run
            svc.m(items);
            svc.m(u);
          }
        }
        """, "Use.run", {}, absent=("Svc.m.Object", "Svc.m.Collection"),
        note="an argument of unknown hierarchy never selects one overload"),
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
          Service(int size) {} //@Service.newInt
          Service(String name) {} //@Service.newString
        }
        class Use {
          void run() { //@Use.run
            new Service();
            new Service("n");
            new java.util.ArrayList<String>();
            new Runnable() { public void run() {} };
          }
        }
        """, "Use.run", {"Service.new": "exact", "Service.newString": "exact"},
        absent=("Service.newInt",),
        note="ADR-75: the arguments select the constructor; an anonymous or external type reaches none"),
    Shape("S7-this-super", """
        class Base {
          Base(int size) {} //@Base.newInt
          Base(String name) {} //@Base.newString
        }
        class Sub extends Base {
          Sub() { //@Sub.new
            this(1);
          }
          Sub(int size) { //@Sub.newInt
            super("s");
          }
        }
        class Use {
          void run() { //@Use.run
            new Sub();
          }
        }
        """, "Sub.new", {"Sub.newInt": "exact"},
        note="ADR-75: this(...) is an exact call; a constructor is not inherited"),
    Shape("S7-super", """
        class Base {
          Base(int size) {} //@Base.newInt
          Base(String name) {} //@Base.newString
        }
        class Sub extends Base {
          Sub(int size) { //@Sub.newInt
            super("s");
          }
        }
        """, "Sub.newInt", {"Base.newString": "exact"},
        note="ADR-75: super(...) is an exact call to the parent's constructor"),
    Shape("S7-overload", """
        class Service {
          Service(Long id) {} //@Service.newLong
          Service(String name) {} //@Service.newString
        }
        class Use {
          void run(Object unknown) { //@Use.run
            new Service(unknown.toString().length() > 0 ? null : null);
          }
        }
        """, "Use.run", {"Service.newLong": "possible", "Service.newString": "possible"},
        note="ADR-75: overloads the arguments cannot separate stay possible"),
    Shape("S8", """
        class Service {
          void init() {} //@Service.init
          void seed() {} //@Service.seed
          void boot() {} //@Service.boot
        }
        class Use {
          Service s = new Service();
          Runnable r = () -> s.seed();
          Use() { //@Use.new
            s.init();
          }
          Use(int size) { //@Use.newInt
            this();
          }
          {
            s.boot();
          }
        }
        """, "Use.new", {"Service.init": "exact", "Service.seed": "exact", "Service.boot": "exact"},
        note="ADR-75: field and instance initializers run in every constructor"),
    Shape("S8-delegating", """
        class Service {
          void init() {} //@Service.init
        }
        class Use {
          Service s = new Service();
          { s.init(); }
          Use() {} //@Use.new
          Use(int size) { //@Use.newInt
            this();
          }
        }
        """, "Use.newInt", {"Use.new": "exact"}, absent=("Service.init",),
        note="ADR-75: a constructor that delegates to this(...) runs no initializer itself"),
    Shape("S8-implicit", """
        class Service {
          void init() {} //@Service.init
        }
        class Use { //@Use.new
          Service s = make();
          static Service make() { return new Service(); } //@Use.make
        }
        class Caller {
          void run() { //@Caller.run
            new Use();
          }
        }
        """, "Use.new", {"Use.make": "exact"},
        note="ADR-75: a class without a constructor gets one when its initializers call"),
    Shape("S8-implicit-target", """
        class Service {
          void init() {} //@Service.init
        }
        class Use { //@Use.new
          Service s = new Service();
          { s.init(); }
        }
        class Caller {
          void run() { //@Caller.run
            new Use();
          }
        }
        """, "Caller.run", {"Use.new": "exact"},
        note="ADR-75: new reaches the implicit constructor"),
    Shape("S8-static", """
        class Service {
          static int count() { return 1; } //@Service.count
          static void warm() {} //@Service.warm
        }
        class Use { //@Use.clinit
          static final int TOTAL = Service.count();
          static {
            Service.warm();
          }
        }
        """, "Use.clinit", {"Service.count": "exact", "Service.warm": "exact"},
        note="ADR-75: static initializer code belongs to <clinit>"),
    Shape("S9", """
        class Service {
          void log(String fmt, Object... args) {} //@Service.log
        }
        class Use {
          Service service;
          void call() { //@Use.call
            service.log("a {} {}", 1, 2);
            service.log("a");
          }
        }
        """, "Use.call", {"Service.log": "exact"},
        note="review S14: a varargs call of any arity from the fixed count up"),
    Shape("S9-fixed-arity-first", """
        class Service {
          void log(String fmt, Object... args) {} //@Service.log.varargs
          void log(String fmt, int a, int b) {} //@Service.log.fixed
        }
        class Use {
          Service service;
          void call() { //@Use.call
            service.log("a {} {}", 1, 2);
          }
        }
        """, "Use.call", {"Service.log.fixed": "exact"},
        note="a fixed-arity overload that applies wins over varargs"),
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
        """, "Base.template",
        {"Base.step": "possible", "SubA.step": "possible", "SubB.step": "possible"},
        note="BL-28: the abstract declaration is a declared row beside the bodies"),
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
    Shape("H13", """
        interface I {
          default void go() {} //@I.go
        }
        abstract class Abs {
          public void go() {} //@Abs.go
        }
        class C extends Abs implements I {
        }
        class Use {
          void run(I i) { //@Use.run
            i.go();
          }
        }
        """, "Use.run", {"Abs.go": "exact"},
        note="review S12: the superclass method wins over the interface default"),
    Shape("H13-default-still-inherited", """
        interface I {
          default void go() {} //@I.go
        }
        abstract class Abs {
          public void go() {} //@Abs.go
        }
        class C extends Abs implements I {
        }
        class D implements I {
        }
        class Use {
          void run(I i) { //@Use.run
            i.go();
          }
        }
        """, "Use.run", {"Abs.go": "possible", "I.go": "possible"},
        note="the default stays a target while one class inherits it"),
    Shape("H14", """
        interface Shape {
          double area(); //@Shape.area
        }
        class Circle implements Shape {
          public double area() { return 1; } //@Circle.area
        }
        class Square implements Shape {
          public double area() { return 2; } //@Square.area
        }
        class Use {
          Shape shape;
          void run() { //@Use.run
            shape.area();
          }
        }
        """, "Use.run", {"Shape.area": "possible", "Circle.area": "possible", "Square.area": "possible"},
        note="BL-28: without an exact target, the declaration joins the possible bodies"),
    Shape("H14-wider-declaration", """
        interface Shape {
          double area(); //@Shape.area
        }
        interface Round extends Shape {}
        class Circle implements Round {
          public double area() { return 1; } //@Circle.area
        }
        class Oval implements Round {
          public double area() { return 3; } //@Oval.area
        }
        class Square implements Shape {
          public double area() { return 2; } //@Square.area
        }
        class Use {
          Round round;
          void run() { //@Use.run
            round.area();
          }
        }
        """, "Use.run", {"Circle.area": "possible", "Oval.area": "possible"},
        absent=("Shape.area", "Square.area"),
        note="BL-28: a declaration whose dispatch reaches a body the call cannot is no row"),
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
        pending="not planned: no dispatch from an external class to a source override"),
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
        note="BL-26: a body whose parameter is a type variable is indexed by its erasure"),
    Shape("G3-bounded", """
        abstract class Shape {}
        class Circle extends Shape {}
        interface Sink {
          void accept(Circle c); //@Sink.accept
        }
        class Generic<T extends Circle> implements Sink {
          public void accept(T t) {} //@Generic.accept
        }
        class Use {
          Sink sink;
          void run() { //@Use.run
            sink.accept(new Circle());
          }
        }
        """, "Use.run", {"Generic.accept": "exact", "Sink.accept": "possible"},
        note="BL-26: the erasure of T is its bound, so accept(T) overrides accept(Circle)"),
    Shape("G3-two", """
        interface Handler<T> {
          void handle(T t); //@Handler.handle
        }
        class Generic<T> implements Handler<T> {
          public void handle(T t) {} //@Generic.handle
        }
        class Strings implements Handler<String> {
          public void handle(String s) {} //@Strings.handle
        }
        class Use {
          Handler<String> h;
          void run() { //@Use.run
            h.handle("x");
          }
        }
        """, "Use.run", {"Generic.handle": "possible", "Strings.handle": "possible"},
        partial=True,
        note="BL-26: an erased body beside a concrete one keeps both possible"),
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
        """, "Use.run", {"Shape.area": "possible", "Circle.area": "possible", "Square.area": "possible"},
        note="BL-26: a receiver typed by a type variable is typed by its bound; BL-28: declared row"),
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
        note="BL-26: a field typed by a type variable is typed by its bound"),
    Shape("G4-object", """
        class Circle {
          public String toString() { return "c"; } //@Circle.toString
          double area() { return 1; } //@Circle.area
        }
        class Use {
          <T extends Circle> void run(T t) { //@Use.run
            t.toString();
            t.area();
          }
        }
        """, "Use.run", {"Circle.area": "exact"},
        note="ADR-81 rule 4: an Object method on a type-variable receiver has no edge"),
    Shape("G4-unbounded", """
        class Circle {
          public String toString() { return "c"; } //@Circle.toString
        }
        class Holder<T> {
          T value;
          void run() { //@Holder.run
            value.toString();
          }
        }
        """, "Holder.run", {},
        note="BL-26: a type variable without a bound stays untyped"),
    Shape("G5", """
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        class Box<T> {
          T value;
          T get() { return value; } //@Box.get
          Box<T> self() { return this; } //@Box.self
        }
        interface Repo<T> {
          T find(); //@Repo.find
        }
        interface CircleRepo extends Repo<Circle> {}
        class CircleBox extends Box<Circle> {}
        class Use {
          Box<Circle> box;
          CircleRepo repo;
          CircleBox circles;
          void run(Box<? extends Circle> some) { //@Use.run
            box.get().area();
            repo.find().area();
            circles.get().area();
            box.value.area();
            box.self().get().area();
            new Box<Circle>().get().area();
            some.get().area();
          }
        }
        """, "Use.run", {
            "Box.get": "exact", "Box.self": "exact", "Repo.find": "possible", "Circle.area": "exact",
        }, note="BL-26: the written argument of a field, parameter, creation, extends clause or return type"),
    Shape("G5-inherited", """
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        class Base<T> {
          T value;
          T get() { return value; } //@Base.get
        }
        class Middle<U> extends Base<U> {}
        class CircleBox extends Middle<Circle> {
          void run() { //@CircleBox.run
            value.area();
            get().area();
            this.value.area();
          }
        }
        """, "CircleBox.run", {"Base.get": "exact", "Circle.area": "exact"},
        note="BL-26: arguments substituted through two extends clauses"),
    Shape("G5-unwritten", """
        import java.util.List;
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        class Box<T> {
          T get() { return null; } //@Box.get
        }
        class Use {
          Box raw;
          Box<?> any;
          Box<? super Circle> lower;
          Box<List<Circle>> nested;
          Box<Box<Circle>> boxes;
          void run() { //@Use.run
            raw.get().area();
            any.get().area();
            lower.get().area();
            nested.get().get(0).area();
            boxes.get().get().area();
          }
        }
        """, "Use.run", {"Box.get": "exact"},
        note="BL-26: a raw unbounded, wildcard or nested argument gives nothing"),
    Shape("G5-raw-bounded", """
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        class Box<T extends Shape> {
          T get() { return null; } //@Box.get
        }
        class Use {
          Box raw;
          void run() { //@Use.run
            raw.get().area();
          }
        }
        """, "Use.run", {
            "Box.get": "exact", "Shape.area": "possible",
            "Circle.area": "possible", "Square.area": "possible",
        },
        note="BL-26: a raw type erases T to its bound"),
    Shape("G6", """
        import java.util.List;
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        class Box<T> {
          T get() { return null; } //@Box.get
        }
        class Use {
          List<Circle> circles;
          Box<Circle> box;
          static <T> T first(List<T> items) { return items.get(0); } //@Use.first
          static <T> T same(T item) { return item; } //@Use.same
          static <T> T open(Box<? extends T> box) { return box.get(); } //@Use.open
          void run() { //@Use.run
            first(circles).area();
            same(new Circle()).area();
            open(box).area();
            Use.<Circle>same(null).area();
          }
        }
        """, "Use.run", {
            "Use.first": "exact", "Use.same": "exact", "Use.open": "exact", "Circle.area": "exact",
        }, note="BL-26: a generic method's type argument from its arguments or written"),
    Shape("G6-bound", """
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
          static <T extends Shape> T make() { return null; } //@Use.make
          void run() { //@Use.run
            make().area();
          }
        }
        """, "Use.run", {
            "Use.make": "exact", "Shape.area": "possible",
            "Circle.area": "possible", "Square.area": "possible",
        },
        note="BL-26: a type variable no parameter names takes its bound"),
    Shape("G6-unproven", """
        import java.util.List;
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
          List raw;
          static <T> T first(List<T> items) { return items.get(0); } //@Use.first
          static <T> T pick(T a, T b) { return a; } //@Use.pick
          @SafeVarargs static <T> T any(T... items) { return items[0]; } //@Use.any
          void run(Object o) { //@Use.run
            first(raw).area();
            pick(new Circle(), new Square()).area();
            any(new Circle()).area();
          }
        }
        """, "Use.run", {"Use.first": "exact", "Use.pick": "exact", "Use.any": "exact"},
        note="BL-26: no inference from a raw argument, two types (no least upper bound) or varargs"),
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
        """, "Use.run", {"Circle.area": "exact"}),
    Shape("T2-scope", """
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
          Square c;
          void run(Shape s, boolean flag) { //@Use.run
            if (s instanceof Circle c) {
            } else {
              c.area();
            }
            if (!(s instanceof Circle c)) {
              c.area();
            }
            if (s instanceof Circle c || flag) {
              c.area();
            }
          }
        }
        """, "Use.run", {"Square.area": "exact"},
        note="BL-23: outside the branch where its instanceof is true, the name is the field"),
    Shape("T2-and", """
        abstract class Shape {
          abstract double area(); //@Shape.area
        }
        class Circle extends Shape {
          double area() { return 1; } //@Circle.area
        }
        class Square extends Shape {
          double area() { return 2; } //@Square.area
        }
        record Pair(Circle left, Square right) {}
        class Use {
          double run(Object o) { //@Use.run
            if (o instanceof Pair(Circle l, var r)) {
              l.area();
            }
            return o instanceof Circle c && c.area() > 0 ? 1 : 0;
          }
        }
        """, "Use.run", {"Circle.area": "exact"},
        note="BL-23: the right operand of && and a record pattern component"),
    Shape("T2-flow", """
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
          Square c;
          Square d;
          void run(Shape s) { //@Use.run
            if (!(s instanceof Circle d)) {
            } else {
              d.area();
            }
            if (!(s instanceof Circle c)) {
              return;
            }
            c.area();
          }
        }
        """, "Use.run", {"Circle.area": "exact"},
        note="BL-23: the else branch of a negated instanceof and the statements after an early return"),
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
        """, "Use.run", {"Circle.area": "exact"}),
    Shape("T3-group", """
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
            switch (s) {
              case Square q when q.area() > 0:
                break;
              default:
                break;
            }
          }
        }
        """, "Use.run", {"Square.area": "exact"},
        note="BL-23: a statement-group label and its guard"),
    Shape("T4", """
        class Service {
          void go() {} //@Service.go
        }
        class Use {
          Service b;
          void run() { //@Use.run
            var a = b;
            var b = a;
            b.go();
          }
        }
        """, "Use.run", {"Service.go": "exact"},
        note="review S1: two var locals naming each other through a field recursed forever"),
    Shape("T4-later-local", """
        class Service {
          void go() {} //@Service.go
        }
        class Other {
          void go() {} //@Other.go
        }
        class Use {
          Service y;
          void run() { //@Use.run
            var x = y;
            {
              Other y = new Other();
              x.go();
            }
          }
        }
        """, "Use.run", {"Service.go": "exact"},
        note="review S2: the initializer names the field, not a later local"),
    Shape("T5", """
        interface Callback {
          void on(Other s);
        }
        class Service {
          void go() {} //@Service.go
        }
        class Other {
          void go() {} //@Other.go
        }
        class Use {
          void run(Service s) { //@Use.run
            Callback c = new Callback() {
              public void on(Other s) {
                s.go();
              }
            };
          }
        }
        """, "Use.run", {"Other.go": "exact"},
        note="review S5: the anonymous method's parameter hides the outer one"),
    Shape("T5-anonymous-local", """
        interface Task {
          void exec();
        }
        class Service {
          void go() {} //@Service.go
        }
        class Other {
          void go() {} //@Other.go
        }
        class Use {
          void run(Service s) { //@Use.run
            Task u = new Task() {
              public void exec() {
                Other s = new Other();
                s.go();
              }
            };
          }
        }
        """, "Use.run", {"Other.go": "exact"},
        note="review S5: a local of the anonymous method hides the outer parameter"),
    Shape("T5-anonymous-field", """
        interface Task {
          void exec();
        }
        class Service {
          void go() {} //@Service.go
        }
        class Other {
          void go() {} //@Other.go
        }
        class Use {
          Service helper;
          void run() { //@Use.run
            Task t = new Task() {
              Other helper = new Other();
              public void exec() {
                helper.go();
              }
            };
          }
        }
        """, "Use.run", {"Other.go": "exact"},
        note="review S6: the anonymous class's field hides the outer field"),
    Shape("T5-inner-outer-field", """
        class Service {
          void go() {} //@Service.go
        }
        class Use {
          Service service;
          class Inner {
            void run() { //@Inner.run
              service.go();
            }
          }
        }
        """, "Inner.run", {"Service.go": "exact"},
        note="review S14: a member inner class reads its outer class's fields"),
    Shape("T5-local-class-captures", """
        class Service {
          void go() {} //@Service.go
        }
        class Other {
          void go() {} //@Other.go
        }
        class Use {
          Other helper;
          void run() {
            Service helper = new Service();
            class Local {
              void call() { //@Local.call
                helper.go();
              }
            }
          }
        }
        """, "Local.call", {"Service.go": "exact"},
        note="a local class sees the enclosing method's locals before the outer fields"),
    Shape("T6", """
        class Service {
          void go() {} //@Service.go
        }
        class Other {
          void go() {} //@Other.go
        }
        class Top {
          protected Other helper;
        }
        class Mid extends Top {
          protected Service helper;
        }
        class Child extends Mid {
          void run() { //@Child.run
            helper.go();
          }
        }
        """, "Child.run", {"Service.go": "exact"},
        note="review S11: the nearest field in the hierarchy hides the farther one"),
    Shape("T6-qualified-and-getter", """
        import lombok.Getter;
        class Service {
          void go() {} //@Service.go
        }
        class Other {
          void go() {} //@Other.go
        }
        class Atop {
          @Getter protected Other helper;
        }
        class Zmid extends Atop {
          @Getter protected Service helper;
        }
        class Holder extends Zmid {
        }
        class Use {
          void run(Holder h) { //@Use.run
            h.helper.go();
            h.getHelper().go();
          }
        }
        """, "Use.run", {"Service.go": "exact"},
        note="review S11: field access and Lombok getters read the nearest field"),
    Shape("T7", {
        f"{_APP}A.java": """
            package p;
            import p.q.Validator;
            class Use {
              Validator validator;
              void first() { //@Use.first
                class Validator {
                  void check() {} //@Local.check
                }
                new Validator().check();
              }
              void second() { //@Use.second
                validator.check();
              }
            }
            """,
        f"{_APP}q/Validator.java": """
            package p.q;
            public class Validator {
              public void check() {} //@Validator.check
            }
            """,
    }, "Use.second", {"Validator.check": "exact"},
        note="review S7: a local class does not leak to the whole enclosing type"),
    Shape("T7-in-block", {
        f"{_APP}A.java": """
            package p;
            import p.q.Validator;
            class Use {
              void first() { //@Use.first
                class Validator {
                  void check() {} //@Local.check
                }
                new Validator().check();
              }
            }
            """,
        f"{_APP}q/Validator.java": """
            package p.q;
            public class Validator {
              public void check() {} //@Validator.check
            }
            """,
    }, "Use.first", {"Local.check": "exact"},
        note="inside its block, the local class shadows the import"),
    Shape("T8", """
        class Order {
          void ship() {} //@Order.ship
        }
        class Handler<Order> {
          void handle(Order o) { //@Handler.handle
            o.ship();
          }
        }
        class Bus {
          <Order> void publish(Order event) { //@Bus.publish
            event.ship();
          }
        }
        """, "Handler.handle", {}, absent=("Order.ship",),
        note="review S8: a class type parameter hides the source type"),
    Shape("T8-method", """
        class Order {
          void ship() {} //@Order.ship
        }
        class Bus {
          <Order> void publish(Order event) { //@Bus.publish
            event.ship();
          }
        }
        """, "Bus.publish", {}, absent=("Order.ship",),
        note="review S8: a method type parameter hides the source type"),
    Shape("T8-dispatch", """
        class Order {}
        interface Handler<Order> {
          void handle(Order o); //@Handler.handle
        }
        class OrderHandler implements Handler<Order> {
          public void handle(Order o) {} //@OrderHandler.handle
        }
        class TextHandler implements Handler<String> {
          public void handle(String s) {} //@TextHandler.handle
        }
        class Use {
          Handler<String> handler;
          void run() { //@Use.run
            handler.handle("x");
          }
        }
        """, "Use.run", {
            "Handler.handle": "possible",
            "TextHandler.handle": "possible", "OrderHandler.handle": "possible",
        },
        note="review S8: the contract's signature is generic, so it does not bind to OrderHandler alone"),
    Shape("T9", """
        import java.util.List;
        import java.util.Map;
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
          List<Circle> circles;
          Map<String, Square> byName;
          void run() { //@Use.run
            for (var c : circles) {
              c.area();
            }
            for (var s : byName.values()) {
              s.area();
            }
          }
        }
        """, "Use.run", {"Circle.area": "exact", "Square.area": "exact"}),
    Shape("T9-unwritten", """
        import java.util.List;
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
          List raw;
          List<?> any;
          List<Circle> circles;
          Circle c;
          void run() { //@Use.run
            for (var c : raw) {
              c.area();
            }
            for (var c : any) {
              c.area();
            }
            for (var c : circles.stream().map(x -> x).toList()) {
              c.area();
            }
          }
        }
        """, "Use.run", {},
        note="BL-23: no written type argument, no type; the loop variable still shadows the field"),
    Shape("T10", """
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
          Circle a;
          Circle b;
          void run(boolean flag) { //@Use.run
            (flag ? a : b).area();
            var c = flag ? a : b;
            c.area();
          }
        }
        """, "Use.run", {"Circle.area": "exact"}),
    Shape("T10-mixed", """
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
          Circle a;
          Square b;
          void run(boolean flag) { //@Use.run
            (flag ? a : b).area();
          }
        }
        """, "Use.run", {},
        note="BL-23: branches of different types leave the ternary untyped"),
    Shape("T11", """
        class Service {
          void go() {} //@Service.go
        }
        class Outer {
          Service service;
          void helper() {} //@Outer.helper
          class Inner {
            void helper() {} //@Inner.helper
            void run() { //@Inner.run
              Outer.this.helper();
              Outer.this.service.go();
            }
          }
        }
        """, "Inner.run", {"Outer.helper": "exact", "Service.go": "exact"}),
    Shape("T12", """
        import java.util.Iterator;
        import java.util.List;
        import java.util.Map;
        import java.util.Optional;
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
          List<? extends Circle> circles;
          Optional<Circle> maybe;
          Map<String, Circle> byName;
          void run(Map.Entry<String, Circle> entry, Iterator<Circle> it) { //@Use.run
            circles.get(0).area();
            circles.getFirst().area();
            maybe.orElseThrow().area();
            maybe.orElse(null).area();
            byName.get("x").area();
            byName.values().iterator().next().area();
            entry.getValue().area();
            it.next().area();
            circles.stream().filter(c -> true).findFirst().get().area();
          }
        }
        """, "Use.run", {"Circle.area": "exact"}),
    Shape("T12-unwritten", """
        import java.util.List;
        import java.util.Map;
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
          List raw;
          List<?> any;
          List<Circle> circles;
          Map<String, Circle> byName;
          void run() { //@Use.run
            raw.get(0).area();
            any.get(0).area();
            circles.stream().map(c -> c).findFirst().get().area();
            byName.keySet().iterator().next().area();
          }
        }
        """, "Use.run", {},
        note="BL-27: a raw or wildcard container, or a type-changing chain, gives nothing"),
    Shape("T13", """
        interface Shape {
          double area(); //@Shape.area
        }
        class Circle implements Shape {
          public double area() { return 1; } //@Circle.area
        }
        class Square implements Shape {
          public double area() { return 2; } //@Square.area
        }
        class Use {
          private final Shape field = new Circle();
          static final Shape SHARED = new Circle();
          void run() { //@Use.run
            Shape local = new Circle();
            local.area();
            field.area();
            this.field.area();
            SHARED.area();
            for (int i = 0; i < 2; i++) {
              Shape each = new Circle();
              each.area();
            }
          }
        }
        """, "Use.run", {"Circle.area": "exact", "Shape.area": "possible"},
        note="BL-29: a local or a final field created once as a named class dispatches on it"),
    Shape("T13-kept", """
        interface Shape {
          double area(); //@Shape.area
        }
        class Circle implements Shape {
          public double area() { return 1; } //@Circle.area
        }
        class Square implements Shape {
          public double area() { return 2; } //@Square.area
        }
        class Base {
          protected final Shape inherited = new Circle();
        }
        class Use extends Base {
          Shape open = new Circle();
          final Shape blank;
          final Shape reset = new Circle();
          Use() {
            blank = new Circle();
            this.reset = new Square();
          }
          void run(Shape param) { //@Use.run
            param.area();
            open.area();
            blank.area();
            reset.area();
            inherited.area();
            Shape later = new Circle();
            later = new Square();
            later.area();
            Shape compound = new Circle();
            compound += 1;
            compound.area();
            Shape counted = new Circle();
            counted++;
            counted.area();
            Shape assigned;
            assigned = new Circle();
            assigned.area();
            Shape captured = new Circle();
            Runnable task = new Runnable() {
              public void run() { captured = new Square(); }
            };
            captured.area();
            Shape lambda = new Circle();
            Runnable again = () -> { lambda = new Square(); };
            lambda.area();
            Shape anonymous = new Shape() { public double area() { return 0; } };
            anonymous.area();
            Shape made = () -> 0;
            made.area();
          }
        }
        """, "Use.run", {"Shape.area": "possible", "Circle.area": "possible", "Square.area": "possible"},
        note="BL-29: a parameter, a non-final, blank, reassigned or inherited field, a local "
             "assigned again (compound, ++, in an inner class or a lambda), an anonymous class "
             "or a lambda keep the declared type; the fixture is parsed, not compiled"),
    Shape("T13-overridden", """
        interface Shape {
          double area(); //@Shape.area
        }
        class Circle implements Shape {
          public double area() { return 1; } //@Circle.area
        }
        class Big extends Circle {
          public double area() { return 4; } //@Big.area
        }
        class Square implements Shape {
          public double area() { return 2; } //@Square.area
        }
        class Use {
          void run() { //@Use.run
            Shape local = new Circle();
            local.area();
          }
        }
        """, "Use.run", {"Circle.area": "possible", "Big.area": "possible"},
        absent=("Square.area", "Shape.area"),
        note="BL-29: the dispatch on the created class stays open over its subclasses (ADR-81)"),
    Shape("T13-record", """
        interface Named {
          String name(); //@Named.name
        }
        record R(String name) implements Named {}
        class Other implements Named {
          public String name() { return "o"; } //@Other.name
        }
        class Use {
          void run() { //@Use.run
            Named named = new R("r");
            named.name();
          }
        }
        """, "Use.run", {"Named.name": "possible", "Other.name": "possible"},
        note="BL-29: a created record has no indexed accessor body, so the call keeps "
             "the declared type's dispatch; nothing is exact"),
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
        """, "Use.run", {"Port.send": "possible", "A1.send": "possible", "A2.send": "possible"}),
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
        pending="not planned: a lambda implementation keeps the named body exact"),
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
        pending="not planned: an anonymous subclass keeps the base body exact"),
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
    Shape("F5", """
        class Circle {
          double area() { return 1; } //@Circle.area
        }
        interface Handler {
          void on(Circle c);
        }
        class Use {
          void register(Handler h) {} //@Use.register
          void run() { //@Use.run
            register(c -> c.area());
          }
        }
        """, "Use.run", {"Use.register": "exact", "Circle.area": "exact"},
        note="BL-24: the lambda takes the parameter types of Handler.on"),
    Shape("F5-generic", """
        class Circle {
          double area() { return 1; } //@Circle.area
        }
        interface Handler<T> {
          void on(T value);
        }
        interface CircleHandler extends Handler<Circle> {
          default void log() {}
          boolean equals(Object other);
        }
        class Bus<E> {
          void register(Handler<E> h) {} //@Bus.register
          void each(CircleHandler h) {} //@Bus.each
        }
        class Use {
          Bus<Circle> bus;
          void run() { //@Use.run
            bus.register(c -> c.area());
            bus.each(c -> c.area());
          }
        }
        """, "Use.run", {"Bus.register": "exact", "Bus.each": "exact", "Circle.area": "exact"},
        note="BL-24: type arguments reach the abstract method through the receiver and extends"),
    Shape("F5-unproven", """
        class Circle {
          double area() { return 1; } //@Circle.area
        }
        class Square {
          double area() { return 2; } //@Square.area
        }
        interface OnCircle {
          void on(Circle c);
        }
        interface OnSquare {
          void on(Square s);
        }
        interface Two {
          void on(Circle c);
          void off(Circle c);
        }
        interface Open<T> {
          void on(T value);
        }
        interface Lib extends com.example.Listener {
          void on(Circle c);
        }
        class Use {
          void register(OnCircle h) {} //@Use.registerCircle
          void register(OnSquare h) {} //@Use.registerSquare
          void two(Two h) {} //@Use.two
          <T> void open(Open<T> h) {} //@Use.open
          void lib(Lib h) {} //@Use.lib
          void run() { //@Use.run
            register(c -> c.area());
            two(c -> c.area());
            open(c -> c.area());
            lib(c -> c.area());
          }
        }
        """, "Use.run", {}, partial=True, absent=("Circle.area", "Square.area"),
        note="BL-24: disagreeing overloads, two abstract methods, an unproven type "
             "argument or an external superinterface leave the parameter untyped"),
    Shape("F5-reference", """
        import java.util.List;
        import java.util.function.Consumer;
        class Circle {
          double area() { return 1; } //@Circle.area
        }
        interface Handler {
          void on(Circle c);
        }
        class Sink {
          void handle(Circle c) {} //@Sink.handle
          void handle(Circle c, int n) {} //@Sink.handleTwo
        }
        class Use {
          List<Circle> circles;
          Sink sink;
          void register(Handler h) {}
          void accept(Consumer<Circle> c) {}
          void run() { //@Use.run
            circles.forEach(sink::handle);
            register(sink::handle);
            accept(this.sink::handle);
          }
        }
        """, "Use.run", {"Sink.handle": "exact"}, partial=True, absent=("Sink.handleTwo",),
        note="BL-24: the target's parameter count picks the overload, JDK or source"),
    Shape("F5-reference-unknown", """
        import com.example.Bus;
        class Circle {
          double area() { return 1; } //@Circle.area
        }
        interface Pair {
          void on(Sink sink, Circle c);
        }
        class Sink {
          void handle(Circle c) {} //@Sink.handle
          void handle(Circle c, int n) {} //@Sink.handleTwo
        }
        class Use {
          Bus bus;
          Sink sink;
          void pair(Pair p) {}
          void run() { //@Use.run
            bus.register(sink::handle);
            Runnable r = sink::handle;
            pair(Sink::handle);
          }
        }
        """, "Use.run", {}, partial=True, absent=("Sink.handle", "Sink.handleTwo"),
        note="BL-24: an unknown target, or a type name that fits two arities, stays unresolved"),
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
        pending="not planned: source-type dispatch leaves the caller's visible modules"),
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
        pending="not planned: dependencies do not choose between two declarations of one type"),
    Shape("M4", {
        "app/src/main/java/p/Use.java": """
            package p;
            public class Use {
              Clock clock;
              void run() { //@Use.run
                clock.now();
              }
            }
            """,
        "app/src/main/java/p/Clock.java": """
            package p;
            public class Clock {
              public long now() { //@Clock.main
                return 0;
              }
            }
            """,
        "app/src/test/java/p/Clock.java": """
            package p;
            public class Clock {
              public long now() { //@Clock.test
                return 1;
              }
            }
            """,
    }, "Use.run", {"Clock.main": "exact"},
        note="review S14: production code resolves to its module's src/main type"),
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
        pending="not planned: @Qualifier does not narrow the candidates"),
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
        pending="not planned: publishEvent reaches no @EventListener"),
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
        note="the calls are edges; making these methods flow roots is not planned"),
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
    spec = Path(__file__).resolve().parents[1] / "docs/spec-tech/indexing.md"
    text = spec.read_text(encoding="utf-8")
    section = text[text.index("## Call shapes"):text.index("## Build layout")]
    rows = set(re.findall(r"^\| ([SHGTFMW]\d+) \|", section, re.MULTILINE))
    cases = {shape.row.split("-", 1)[0] for shape in SHAPES}
    assert rows == cases, f"rows without a case: {sorted(rows - cases)}; cases without a row: {sorted(cases - rows)}"
