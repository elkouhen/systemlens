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
    Shape("S1-return-across-ancestors", """
        class Repo {
          void get() {} //@Repo.get
        }
        class Cache {
          void get() {} //@Cache.get
        }
        class Base {
          Repo a(Integer i) { return null; } //@Base.a
          Object b(String s) { return null; } //@Base.b
        }
        class Child extends Base {
          Cache a(String s) { return null; } //@Child.a
          Cache b(String s) { return null; } //@Child.b
        }
        class Use {
          Child child;
          void run() { //@Use.run
            child.a(1).get();
            child.b("s").get();
          }
        }
        """, "Use.run",
        {"Base.a": "exact", "Repo.get": "exact", "Child.b": "exact", "Cache.get": "exact"},
        note="BC-07: the return type comes from the overload Java selects among the receiver's and its "
             "ancestors' declarations; an override's return hides its ancestor's"),
    Shape("S1-dotted-generic-type", """
        import java.util.*;
        class Order {
          void ship() {} //@Order.ship
        }
        class Outer<A> {
          class Inner<B> {
            void z() {} //@Outer.Inner.z
          }
          void z() {} //@Outer.z
        }
        class Use {
          void run(Map<String, Order> m, Outer<String>.Inner<Order> i) { //@Use.run
            for (Map.Entry<String, Order> e : m.entrySet()) {
              e.getValue().ship();
            }
            i.z();
          }
        }
        """, "Use.run", {"Order.ship": "exact", "Outer.Inner.z": "exact"},
        note="BC-16: Map.Entry resolves through import java.util.*; type arguments are erased by "
             "bracket depth"),
    Shape("S1-this-chain", """
        class Api {
          void call() {} //@Api.call
        }
        class Client {
          Api api;
          Api next() { return api; } //@Client.next
        }
        class Use {
          Client client;
          void run() { //@Use.run
            this.client.api.call();
            this.client.next().call();
          }
        }
        """, "Use.run", {"Api.call": "exact", "Client.next": "exact"},
        note="BC-17: only this.field reads the innermost class's field; this.a.b is a field access on it"),
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
    Shape("S2-import-shadowed", {
        f"{_APP}l/Util.java": """
            package p.l;
            public class Util {
              public static void foo() {} //@Util.foo
            }
            """,
        f"{_APP}a/Use.java": """
            package p.a;
            import static p.l.Util.foo;
            class Extended extends org.acme.ExternalBase {
              void run() { //@Extended.run
                foo();
              }
            }
            """,
    }, "Extended.run", {}, absent=("Util.foo",),
        note="BC-13: an external supertype with unknown members may declare foo(), which would "
             "shadow the static import"),
    Shape("S2-import-shadowed-jdk", {
        f"{_APP}l/Util.java": """
            package p.l;
            public class Util {
              public static void foo() {} //@Util.foo
            }
            """,
        f"{_APP}a/Use.java": """
            package p.a;
            import static p.l.Util.foo;
            import java.util.AbstractList;
            abstract class Listed extends AbstractList<String> {
              void run() { //@Listed.run
                foo();
              }
            }
            """,
    }, "Listed.run", {}, absent=("Util.foo",),
        note="BC-13: the pass does not know AbstractList's members, so the call stays unresolved"),
    Shape("S2-import-known-supertype", {
        f"{_APP}l/Util.java": """
            package p.l;
            public class Util {
              public static void foo() {} //@Util.foo
            }
            """,
        f"{_APP}a/Use.java": """
            package p.a;
            import static p.l.Util.foo;
            class Known implements Runnable {
              public void run() { //@Known.run
                foo();
              }
            }
            """,
    }, "Known.run", {"Util.foo": "exact"},
        note="BC-13: Runnable's members are known and do not include foo"),
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
    Shape("S10", """
        import com.lib.Source;
        class Order {}
        class Line {}
        class Taker {
          void take(Order o, int n) {} //@Taker.take.Order
          void take(Line l, int n) {} //@Taker.take.Line
          void take(Order o, String s) {} //@Taker.take.String
        }
        class Use {
          Taker taker;
          Source source;
          void run() { //@Use.run
            taker.take(source.next(), 1);
          }
        }
        """, "Use.run", {"Taker.take.Order": "possible", "Taker.take.Line": "possible"},
        absent=("Taker.take.String",),
        note="ADR-87: every overload no known argument excludes"),
    Shape("S10-typed-argument", """
        import com.lib.Source;
        class Order {}
        class Line {}
        class Taker {
          void take(Order o) {} //@Taker.take.Order
          void take(Line l) {} //@Taker.take.Line
        }
        class Use {
          Taker taker;
          Order order;
          void run() { //@Use.run
            taker.take(order);
          }
        }
        """, "Use.run", {"Taker.take.Order": "exact"}, absent=("Taker.take.Line",),
        note="a typed argument selects one overload, exact as before ADR-87"),
    Shape("S10-method-reference", """
        import com.lib.Source;
        class Order {}
        class Line {}
        class Taker {
          void take(Order o) {} //@Taker.take.Order
          void take(Line l) {} //@Taker.take.Line
        }
        class Use {
          Taker taker;
          Source source;
          void run() { //@Use.run
            source.each(taker::take);
          }
        }
        """, "Use.run", {}, absent=("Taker.take.Order", "Taker.take.Line"),
        note="a method reference with unproven parameter types stays unresolved (BL-37)"),
    Shape("S10-interface", """
        import com.lib.Source;
        class Order {}
        class Line {}
        interface Taker {
          void take(Order o); //@Taker.take.Order
          void take(Line l); //@Taker.take.Line
        }
        class FastTaker implements Taker {
          public void take(Order o) {} //@FastTaker.take.Order
          public void take(Line l) {} //@FastTaker.take.Line
        }
        class Use {
          Taker taker;
          Source source;
          void run() { //@Use.run
            taker.take(source.next());
          }
        }
        """, "Use.run", {"FastTaker.take.Order": "possible", "FastTaker.take.Line": "possible"},
        absent=("Taker.take.Order", "Taker.take.Line"),
        note="each overload dispatches to its implementations; no declared row"),
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
    Shape("S5-implicit-supertypes", """
        enum Color { RED }
        record Pt(int x) {}
        class Svc {
          void o(Object x) {} //@Svc.o.Object
          void o(Enum<?> x) {} //@Svc.o.Enum
          void m(Enum<?> x) {} //@Svc.m.Enum
          void m(String x) {} //@Svc.m.String
          void r(Object x) {} //@Svc.r.Object
          void r(Record x) {} //@Svc.r.Record
          void c(Comparable<?> x) {} //@Svc.c.Comparable
          void c(Runnable x) {} //@Svc.c.Runnable
        }
        class Use {
          Svc svc;
          void run() { //@Use.run
            svc.o(Color.RED);
            svc.m(Color.RED);
            svc.r(new Pt(1));
            svc.c(Color.RED);
          }
        }
        """, "Use.run",
        {"Svc.o.Enum": "exact", "Svc.m.Enum": "exact", "Svc.r.Record": "exact", "Svc.c.Comparable": "exact"},
        note="BC-06: an enum is an Enum, Comparable, Serializable and Constable; a record is a Record"),
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
        """, "Use.run", {"Svc.m.Object": "possible", "Svc.m.Collection": "possible"},
        note="an argument of unknown hierarchy never selects one overload: both stay possible (ADR-87)"),
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
    Shape("S8-interface-field", """
        class Impl {
          static int compute() { return 1; } //@Impl.compute
          Impl(int i) {} //@Impl.init
        }
        interface Defaults { //@Defaults.clinit
          Impl DEFAULT = new Impl(Impl.compute());
        }
        """, "Defaults.clinit", {"Impl.compute": "exact", "Impl.init": "exact"},
        note="BC-14: an interface field initializer is static code of the interface's <clinit>"),
    Shape("S8-enum-constant", """
        class Helper {
          static int helper() { return 1; } //@Helper.helper
          static String text() { return ""; } //@Helper.text
        }
        class X {
          X() {} //@X.init
        }
        enum Kind { //@Kind.clinit
          A(Helper.helper()),
          B(new X()),
          C(Helper.text()) { void f() {} },
          D;
          Kind() {} //@Kind.init
          Kind(int i) {} //@Kind.initInt
          Kind(X x) {} //@Kind.initX
          Kind(String s) {} //@Kind.initString
          void f() {}
        }
        """, "Kind.clinit",
        {"Helper.helper": "exact", "Helper.text": "exact", "X.init": "exact", "Kind.init": "exact",
         "Kind.initInt": "exact", "Kind.initX": "exact"},
        absent=("Kind.initString",),
        note="BC-18: enum constants are static code of the enum's <clinit>; a constant with a body "
             "creates an anonymous class, which reaches no constructor (ADR-62)"),
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
    Shape("S9-varargs-last", """
        class Service {
          void f(int a) {} //@Service.f.int
          void f(Object... a) {} //@Service.f.varargs
          void g(String a, String b) {} //@Service.g.fixed
          void g(Object... a) {} //@Service.g.varargs
        }
        class Use {
          Service service;
          void text() { //@Use.text
            service.f("x");
            service.g(1, 2);
          }
          void number() { //@Use.number
            service.f(1);
            service.g("a", "b");
          }
        }
        """, "Use.text", {"Service.f.varargs": "exact", "Service.g.varargs": "exact"},
        note="BC-08: a varargs overload applies only when no fixed arity does"),
    Shape("S9-varargs-last-fixed", """
        class Service {
          void f(int a) {} //@Service.f.int
          void f(Object... a) {} //@Service.f.varargs
          void g(String a, String b) {} //@Service.g.fixed
          void g(Object... a) {} //@Service.g.varargs
        }
        class Use {
          Service service;
          void number(Unknown u) { //@Use.number
            service.f(1);
            service.g("a", "b");
            service.g(u, u);
          }
        }
        """, "Use.number", {"Service.f.int": "exact", "Service.g.fixed": "exact"},
        note="BC-08: a fixed arity that applies wins over varargs; an untyped argument selects nothing"),
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
    Shape("H2-varargs-overload", """
        class Base {
          void log(String... a) {} //@Base.log
        }
        class Sub extends Base {
          void log(int... a) {} //@Sub.log
        }
        class Use {
          void run(Base b) { //@Use.run
            b.log("x");
          }
        }
        """, "Use.run", {"Base.log": "exact"},
        note="BC-15: log(int...) overloads log(String...); it does not override it"),
    Shape("H2-lombok-boolean", """
        import lombok.Getter;
        class Base {
          public boolean isActive() { return true; } //@Base.isActive
          public boolean isOpen() { return true; } //@Base.isOpen
        }
        @Getter class Gen extends Base {
          private boolean isActive;
        }
        @Getter class Gen2 extends Base {
          private boolean open;
        }
        class Use {
          void run(Base n, Gen g) { //@Use.run
            n.isActive();
            g.isActive();
            n.isOpen();
          }
        }
        """, "Use.run", {"Base.isActive": "possible", "Base.isOpen": "possible"},
        note="BC-15: Lombok writes isActive() for a boolean isActive and isOpen() for a boolean open, "
             "each overriding Base's"),
    Shape("H2-lombok-enum", """
        import lombok.Getter;
        class Service {
          void go() {} //@Service.go
        }
        enum Kind {
          A;
          @Getter private Service service;
        }
        class Use {
          void run(Kind k) { //@Use.run
            k.getService().go();
          }
        }
        """, "Use.run", {"Service.go": "exact"},
        note="BC-15: @Getter on an enum field generates getService(), typed by the field"),
    Shape("H2-lombok-accessors", """
        import lombok.Getter;
        import lombok.experimental.Accessors;
        class Base {
          public String name() { return ""; } //@Base.name
        }
        @Getter @Accessors(fluent = true) class Fluent extends Base {
          private String name;
        }
        class Use {
          void run(Base b) { //@Use.run
            b.name();
          }
        }
        """, "Use.run", {"Base.name": "possible"},
        note="BC-15: @Accessors renames the accessors, so Fluent may generate an override of name()"),
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
    Shape("H9-constant-field", """
        class W {
          void go() {} //@W.go
        }
        class V {
          void go() {} //@V.go
        }
        enum E {
          A {
            W f = new W();
            void x() { f.go(); } //@E.A.x
          };
          V f = new V();
          void x() {}
        }
        """, "E.A.x", {"W.go": "exact"}, absent=("V.go",),
        note="BC-14: a constant body's own field hides the enum's"),
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
        class Quiet extends Thread {
        }
        class Use {
          Thread t;
          void go() { //@Use.go
            t.run();
            t.start();
          }
        }
        """, "Use.go", {"MyRunner.run": "possible"},
        note="BL-31: the external class has its own body and other library subclasses; "
             "a subclass that inherits the call, and a method no subclass overrides, add nothing"),
    Shape("H11-created", """
        class MyRunner extends Thread {
          public void run() {} //@MyRunner.run
        }
        class Other extends Thread {
          public void run() {} //@Other.run
        }
        class Use {
          void go() { //@Use.go
            Thread t = new MyRunner();
            t.run();
          }
        }
        """, "Use.go", {"MyRunner.run": "exact"},
        note="BL-31 with ADR-86: a variable created once as a source subclass dispatches on it"),
    Shape("H11-kept", """
        class Out extends java.io.OutputStream {
          public void write(int b) {} //@Out.write
          public void write(long b) {}
        }
        class Mine extends Thread {
          public void run() {} //@Mine.run
        }
        class Use {
          java.io.OutputStream out;
          com.example.ext.Source source;
          void go() { //@Use.go
            out.write(source.next());
            Thread made = new Thread() { public void run() {} };
            made.run();
          }
        }
        """, "Use.go", {}, absent=("Out.write", "Mine.run"),
        note="BL-31: same-arity overloads the arguments cannot separate stay unresolved; an "
             "anonymous subclass created once implements run itself (BL-30)"),
    Shape("H11-scope", {
        f"{_APP}A.java": """
            package p;
            class Use {
              Thread t;
              void go() { //@Use.go
                t.run();
              }
            }
            """,
        "lib/src/main/java/q/Late.java": """
            package q;
            class Late extends Thread {
              public void run() {} //@Late.run
            }
            """,
        f"{_APP}Mine.java": """
            package p;
            class Mine extends Thread {
              public void run() {} //@Mine.run
            }
            """,
    }, "Use.go", {"Mine.run": "possible"}, absent=("Late.run",),
        note="BL-31: a subclass outside the caller's visible modules is not reached (ADR-71)"),
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
    Shape("G7", """
        interface Handler<T> {
          void handle(T message); //@Handler.handle
        }
        class Text implements Handler<String> {
          public void handle(String message) {} //@Text.handle
        }
        class Bytes implements Handler<byte[]> {
          public void handle(byte[] message) {} //@Bytes.handle
        }
        abstract class Base<T> implements Handler<T> {}
        class Ints extends Base<Integer> {
          public void handle(Integer message) {} //@Ints.handle
        }
        class Use {
          Handler<String> field;
          Handler<String> make() { return null; } //@Use.make
          void run(Handler<String> param, Object raw) { //@Use.run
            field.handle("a");
            this.field.handle("b");
            param.handle("c");
            ((Handler<String>) raw).handle("d");
            make().handle("e");
          }
        }
        """, "Use.run", {"Text.handle": "exact", "Handler.handle": "possible", "Use.make": "exact"},
        note="BL-45: an implementor whose clause argument contradicts the receiver's written "
             "one is excluded, through a generic superclass too; the witness stays"),
    Shape("G7-related", """
        interface Handler<T> {
          void handle(T message); //@Handler.handle
        }
        class Text implements Handler<String> {
          public void handle(String message) {} //@Text.handle
        }
        class Bytes implements Handler<byte[]> {
          public void handle(byte[] message) {} //@Bytes.handle
        }
        class Chars implements Handler<CharSequence> {
          public void handle(CharSequence message) {} //@Chars.handle
        }
        class Objects implements Handler<Object> {
          public void handle(Object message) {} //@Objects.handle
        }
        class Raw implements Handler {
          public void handle(Object message) {} //@Raw.handle
        }
        class Generic<T> implements Handler<T> {
          public void handle(T message) {} //@Generic.handle
        }
        class Lib implements Handler<org.acme.Payload> {
          public void handle(org.acme.Payload message) {} //@Lib.handle
        }
        class Use {
          Handler<String> field;
          void run() { //@Use.run
            field.handle("a");
          }
        }
        """, "Use.run", {
            "Text.handle": "possible", "Chars.handle": "possible", "Objects.handle": "possible",
            "Raw.handle": "possible", "Generic.handle": "possible", "Lib.handle": "possible",
        }, absent=("Bytes.handle",),
        note="BL-45: a supertype argument, Object, a raw clause, a type variable and a library "
             "type keep the implementor"),
    Shape("G7-open", """
        interface Handler<T> {
          void handle(T message); //@Handler.handle
        }
        class Text implements Handler<String> {
          public void handle(String message) {} //@Text.handle
        }
        class Bytes implements Handler<byte[]> {
          public void handle(byte[] message) {} //@Bytes.handle
        }
        class Use<U> {
          Handler<?> any;
          Handler<? extends String> bounded;
          Handler raw;
          Handler<U> variable;
          Handler<Integer> none;
          void run() { //@Use.run
            any.handle(null);
            bounded.handle(null);
            raw.handle("x");
            variable.handle(null);
            none.handle(1);
          }
        }
        """, "Use.run", {"Text.handle": "possible", "Bytes.handle": "possible", "Handler.handle": "possible"},
        note="BL-45: a wildcard, a raw type or a type variable on the receiver keeps every "
             "implementor; a filter that would exclude them all does not apply"),
    Shape("G7-source", """
        interface Sink<T> {
          void put(T item); //@Sink.put
        }
        class Order {}
        class Big extends Order {}
        class Line {}
        class Orders implements Sink<Order> {
          public void put(Order item) {} //@Orders.put
        }
        class Bigs implements Sink<Big> {
          public void put(Big item) {} //@Bigs.put
        }
        class Lines implements Sink<Line> {
          public void put(Line item) {} //@Lines.put
        }
        class Use {
          Sink<Order> sink;
          void run() { //@Use.run
            sink.put(new Order());
          }
        }
        """, "Use.run", {"Orders.put": "possible", "Bigs.put": "possible"}, absent=("Lines.put",),
        partial=True,
        note="BL-45: source arguments related by subtyping keep the implementor; an "
             "unrelated one is excluded"),
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
    Shape("T1-c-style-array", """
        class Foo {
          void m() {} //@Foo.m
        }
        class Main {
          Foo items[];
          void take(Foo f) {} //@Main.take.Foo
          void take(Foo[] f) {} //@Main.take.array
          void run(Foo x[]) { //@Main.run
            take(x);
            Foo xs[] = new Foo[1];
            xs[0].m();
            take(items);
          }
        }
        """, "Main.run", {"Main.take.array": "exact", "Foo.m": "exact"},
        note="BC-11: Foo x[] declares an array, as a parameter, a local or a field"),
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
    Shape("T5-anonymous-this", """
        interface Task {
          void go();
        }
        class Main {
          void register(Runnable r) {} //@Main.register.Runnable
          void register(Main m) {} //@Main.register.Main
          void register(Task t) {} //@Main.register.Task
          void run() { //@Main.run
            new Thread() { public void run() { register(this); } };
            new Task() { public void go() { register(this); } };
            Runnable r = () -> register(this);
          }
        }
        """, "Main.run",
        {"Main.register.Runnable": "exact", "Main.register.Task": "exact", "Main.register.Main": "exact"},
        note="BC-10: this in an anonymous class body is that instance, typed by its supertype; "
             "in a lambda it stays the enclosing class"),
    Shape("T5-declarator-scope", """
        class Foo {
          void m() {} //@Foo.m
          Foo n() { return this; } //@Foo.n
        }
        class Bar {
          void m() {} //@Bar.m
          Bar n() { return this; } //@Bar.n
        }
        class Conn implements AutoCloseable {
          public void close() {}
          void ping() {} //@Conn.ping
        }
        class Other implements AutoCloseable {
          public void close() {}
          void ping() {} //@Other.ping
        }
        class Main {
          Bar a;
          Conn c;
          Other open() { return new Other(); } //@Main.open
          void run() { //@Main.run
            Foo a = new Foo(), b = a.n();
            b.m();
            try (Other c = open()) { c.ping(); } catch (Exception e) { c.ping(); } finally { c.ping(); }
          }
        }
        """, "Main.run",
        {"Foo.n": "exact", "Foo.m": "exact", "Main.open": "exact", "Other.ping": "exact", "Conn.ping": "exact"},
        absent=("Bar.n", "Bar.m"),
        note="BC-11: a declarator sees the earlier ones of its declaration; a resource is out of scope "
             "in catch and finally"),
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
        """, "Use.run", {"Handler.handle": "possible", "TextHandler.handle": "exact"},
        absent=("OrderHandler.handle",),
        note="review S8: the contract's signature is generic, so it does not bind to OrderHandler "
             "alone; Handler<Order> contradicts the receiver's Handler<String> (BL-45)"),
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
          }
        }
        """, "Use.run", {},
        note="BL-23: no written type argument, no type; the loop variable still shadows the field"),
    Shape("T9-array", """
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
          Circle[] circles;
          Circle[] all() { return circles; } //@Use.all
          void run(Square[][] grid) { //@Use.run
            for (var c : circles) {
              c.area();
            }
            for (var c : all()) {
              c.area();
            }
            for (var row : grid) {
              row[0].area();
            }
          }
        }
        """, "Use.run", {"Use.all": "exact", "Circle.area": "exact", "Square.area": "exact"},
        note="BL-42: a var loop over an array takes its element type"),
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
    Shape("T10-null", """
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
            (flag ? a : null).area();
            (flag ? null : a).area();
            var c = flag ? null : a;
            c.area();
          }
        }
        """, "Use.run", {"Circle.area": "exact"},
        note="BL-42: a null branch takes the other branch's type"),
    Shape("T10-null-unproven", """
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
          void none(boolean flag) { //@Use.none
            (flag ? null : null).area();
            (flag ? b : (flag ? a : null)).area();
          }
        }
        """, "Use.none", {},
        note="BL-42: a null branch never reconciles two different types"),
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
            byName.keySet().iterator().next().area();
          }
        }
        """, "Use.run", {},
        note="BL-27: a raw or wildcard container gives nothing; BL-40 types map"),
    Shape("T14", """
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
        class Store {
          Optional<Circle> maybe() { return null; } //@Store.maybe
          Map<String, Circle> index() { return null; } //@Store.index
          static List<Circle> all() { return null; } //@Store.all
        }
        class Use {
          Store store;
          void run() { //@Use.run
            store.maybe().get().area();
            store.index().get("k").area();
            Store.all().get(0).area();
            Store.all().stream().findFirst().get().area();
            for (var c : Store.all()) {
              c.area();
            }
            Store.all().forEach(c -> c.area());
          }
        }
        """, "Use.run", {
            "Store.maybe": "exact", "Store.index": "exact", "Store.all": "exact", "Circle.area": "exact",
        },
        note="BL-35: a source method's declared container return gives the element"),
    Shape("T14-var", """
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
        class Store {
          List<Circle> all() { return null; } //@Store.all
        }
        class Use {
          Store store;
          List<Circle> field;
          void run() { //@Use.run
            var held = store.all();
            held.get(0).area();
            var s = field.stream();
            s.findFirst().get().area();
            var a = field;
            var field = a;
            field.get(0).area();
          }
        }
        """, "Use.run", {"Store.all": "exact", "Circle.area": "exact"},
        note="BL-35: a var takes the view of its initializer, read where it is written"),
    Shape("T14-inherited", """
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
        interface Catalog<T extends Shape> {
          List<T> items(); //@Catalog.items
        }
        abstract class Base<T extends Shape> implements Catalog<T> {
          public abstract List<T> items(); //@Base.items
        }
        abstract class Circles extends Base<Circle> {}
        class CircleStore extends Circles {
          public List<Circle> items() { return null; } //@CircleStore.items
        }
        class Use {
          Circles circles;
          void run() { //@Use.run
            circles.items().get(0).area();
          }
        }
        """, "Use.run", {"CircleStore.items": "exact", "Circle.area": "exact"},
        note="BL-35, review B.4: declarations of one parameter list that agree give the view"),
    Shape("T14-unproven", """
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
        interface Api {
          List<? extends Shape> items(); //@Api.items
        }
        abstract class Mid implements Api {
          public abstract List<Circle> items(); //@Mid.items
        }
        abstract class Leaf extends Mid {}
        class LeafImpl extends Leaf {
          public List<Circle> items() { return null; } //@LeafImpl.items
        }
        class Store {
          List<Circle> find(String name) { return null; } //@Store.findName
          List<Square> find(Integer id) { return null; } //@Store.findId
          List raw() { return null; } //@Store.raw
          List<?> any() { return null; } //@Store.any
        }
        class Use {
          Store store;
          Leaf leaf;
          void run(Object key) { //@Use.run
            leaf.items().get(0).area();
            store.find(key).get(0).area();
            store.raw().get(0).area();
            store.any().get(0).area();
            var r = store.raw();
            r.get(0).area();
          }
        }
        """, "Use.run",
        {"LeafImpl.items": "exact", "Circle.area": "exact", "Store.raw": "exact", "Store.any": "exact"},
        note="BL-35: unseparated overloads, a raw or wildcard return give nothing; BC-07: Mid's "
             "List<Circle> items() hides Api's, as in Java"),
    Shape("T15", """
        enum Status {
          ACTIVE, DONE;
          String label() { return name(); } //@Status.label
          static Status first() { return values()[0]; } //@Status.first
        }
        class Use {
          void run(String s) { //@Use.run
            Status.valueOf(s).label();
            Status.values()[0].label();
            for (var each : Status.values()) {
              each.label();
            }
          }
        }
        """, "Use.run", {"Status.label": "exact"},
        note="BL-42: an enum's implicit valueOf(String) and values()"),
    Shape("T15-unproven", """
        enum Status {
          ACTIVE, DONE;
          String label() { return name(); } //@Status.label
        }
        class Plain {
          String label() { return ""; } //@Plain.label
        }
        class Use {
          void run(String s) { //@Use.run
            Status.valueOf(Status.class, s).label();
            Plain.valueOf(s).label();
            Plain.values()[0].label();
          }
        }
        """, "Use.run", {},
        note="BL-42: Enum.valueOf(Class, String) and a class that is no enum stay untyped"),
    Shape("T16", """
        import java.util.ArrayList;
        import java.util.Arrays;
        import java.util.Collections;
        import java.util.HashMap;
        import java.util.List;
        import java.util.Map;
        import java.util.Objects;
        import java.util.Optional;
        import java.util.concurrent.Callable;
        import java.util.concurrent.CompletableFuture;
        import java.util.function.BiFunction;
        import java.util.function.BinaryOperator;
        import java.util.function.Function;
        import java.util.function.Supplier;
        import java.util.function.UnaryOperator;
        import java.util.stream.Collectors;
        import java.util.stream.Stream;
        import static java.util.Objects.requireNonNull;
        import static java.util.stream.Collectors.toSet;
        class Circle {
          void m01() {} //@Circle.m01
          void m02() {} //@Circle.m02
          void m03() {} //@Circle.m03
          void m04() {} //@Circle.m04
          void m05() {} //@Circle.m05
          void m06() {} //@Circle.m06
          void m07() {} //@Circle.m07
          void m08() {} //@Circle.m08
          void m09() {} //@Circle.m09
          void m10() {} //@Circle.m10
          void m11() {} //@Circle.m11
          void m12() {} //@Circle.m12
          void m13() {} //@Circle.m13
          void m14() {} //@Circle.m14
          void m15() {} //@Circle.m15
          void m16() {} //@Circle.m16
          void m17() {} //@Circle.m17
          void m18() {} //@Circle.m18
          void m19() {} //@Circle.m19
          void m20() {} //@Circle.m20
          void m21() {} //@Circle.m21
          void m22() {} //@Circle.m22
          void m23() {} //@Circle.m23
          void m24() {} //@Circle.m24
          void m25() {} //@Circle.m25
          void m26() {} //@Circle.m26
          void m27() {} //@Circle.m27
          void m28() {} //@Circle.m28
          void m29() {} //@Circle.m29
          void m30() {} //@Circle.m30
        }
        class Use {
          Map<String, Circle> byName;
          List<Circle> circles;
          Circle[] array;
          CompletableFuture<Circle> future;
          Supplier<Circle> supplier;
          Function<String, Circle> function;
          BiFunction<String, Integer, Circle> bi;
          UnaryOperator<Circle> unary;
          BinaryOperator<Circle> binary;
          Callable<Circle> callable;
          void run(String k, Circle c) throws Exception { //@Use.run
            byName.getOrDefault(k, c).m01();
            byName.computeIfAbsent(k, x -> new Circle()).m02();
            byName.put(k, c).m03();
            byName.remove(k).m04();
            circles.stream().toList().get(0).m05();
            circles.stream().collect(Collectors.toList()).get(0).m06();
            circles.stream().collect(toSet()).iterator().next().m07();
            circles.stream().reduce(c, (a, b) -> a).m08();
            List.of(c, c).get(0).m09();
            Map.of(k, c).get(k).m10();
            Arrays.asList(c).get(0).m11();
            Arrays.stream(array).findFirst().get().m12();
            Collections.unmodifiableList(circles).get(0).m13();
            List.copyOf(circles).get(0).m14();
            new ArrayList<>(circles).get(0).m15();
            new HashMap<>(byName).get(k).m16();
            new ArrayList<Circle>().get(0).m17();
            Optional.ofNullable(c).orElseThrow().m18();
            Stream.of(c).findAny().get().m19();
            future.join().m20();
            CompletableFuture.completedFuture(c).get().m21();
            Objects.requireNonNull(c).m22();
            requireNonNull(c, "c").m23();
            java.util.List.of(c).get(0).m24();
            supplier.get().m25();
            function.apply(k).m26();
            bi.apply(k, 1).m27();
            unary.apply(c).m28();
            binary.apply(c, c).m29();
            callable.call().m30();
          }
        }
        """, "Use.run", {"Circle.m01": "exact", "Circle.m02": "exact", "Circle.m03": "exact", "Circle.m04": "exact", "Circle.m05": "exact", "Circle.m06": "exact", "Circle.m07": "exact", "Circle.m08": "exact", "Circle.m09": "exact", "Circle.m10": "exact", "Circle.m11": "exact", "Circle.m12": "exact", "Circle.m13": "exact", "Circle.m14": "exact", "Circle.m15": "exact", "Circle.m16": "exact", "Circle.m17": "exact", "Circle.m18": "exact", "Circle.m19": "exact", "Circle.m20": "exact", "Circle.m21": "exact", "Circle.m22": "exact", "Circle.m23": "exact", "Circle.m24": "exact", "Circle.m25": "exact", "Circle.m26": "exact", "Circle.m27": "exact", "Circle.m28": "exact", "Circle.m29": "exact", "Circle.m30": "exact"},
        note="BL-36: Map accessors, stream results, static factories, creations, futures and functional results"),
    Shape("T16-unproven", """
        import java.util.ArrayList;
        import java.util.List;
        import java.util.Optional;
        import java.util.stream.Collectors;
        import java.util.stream.Stream;
        class Circle {
          double area() { return 1; } //@Circle.area
        }
        class Square {
          double area() { return 2; } //@Square.area
        }
        class Use {
          List<Circle> circles;
          Circle[] array;
          void run(Circle c, Square s) { //@Use.run
            List.of(c, s).get(0).area();
            Stream.of(c, s).findFirst().get().area();
            Optional.ofNullable(null).get().area();
            List.of(array).get(0).area();
            new ArrayList<>().get(0).area();
            new ArrayList<>(10).get(0).area();
            new ArrayList(circles).get(0).area();
            circles.stream().collect(Collectors.toCollection(ArrayList::new)).get(0).area();
          }
        }
        """, "Use.run", {},
        note="BL-36: arguments of different types, an array, a diamond without a typed argument, "
             "a raw creation or another collector stay untyped"),
    Shape("T16-shadowed", """
        import static java.util.Objects.requireNonNull;
        class Circle {
          double area() { return 1; } //@Circle.area
        }
        class Square {
          double area() { return 2; } //@Square.area
        }
        class Use {
          Square requireNonNull(Object o, String message) { return null; } //@Use.requireNonNull
          void run(Circle c) { //@Use.run
            requireNonNull(c, "c").area();
          }
        }
        """, "Use.run", {"Use.requireNonNull": "exact", "Square.area": "exact"},
        note="BL-36: a declared method shadows the static import of Objects.requireNonNull"),
    Shape("T17", """
        import java.util.List;
        import java.util.Map;
        import java.util.Optional;
        class Circle {
          double area() { return 1; } //@Circle.area
        }
        class Repo<T> {
          Map<String, List<T>> groups() { return null; } //@Repo.groups
        }
        class Use {
          Map<String, List<Circle>> byName;
          Map<String, Circle> index;
          List<Optional<Circle>> maybes;
          Repo<Circle> repo;
          void run() { //@Use.run
            byName.get("x").get(0).area();
            maybes.get(0).get().area();
            repo.groups().get("x").get(0).area();
            for (var e : index.entrySet()) {
              e.getValue().area();
            }
            for (var l : byName.values()) {
              l.get(0).area();
            }
          }
        }
        """, "Use.run", {"Circle.area": "exact", "Repo.groups": "exact"},
        note="BL-41: a nested container argument, substituted through one level, and entrySet"),
    Shape("T17-unproven", """
        import java.util.List;
        import java.util.Map;
        class Circle {
          double area() { return 1; } //@Circle.area
        }
        class Box<T> {
          T get() { return null; } //@Box.get
        }
        class Use {
          Map<String, List<List<Circle>>> deep;
          Box<List<Circle>> box;
          Map<String, List<? super Circle>> lower;
          void run() { //@Use.run
            deep.get("x").get(0).get(0).area();
            box.get().get(0).area();
            lower.get("x").get(0).area();
          }
        }
        """, "Use.run", {"Box.get": "exact"}, absent=("Circle.area",),
        note="BL-41: two levels of nesting, a source generic's type variable bound to a container, "
             "and a lower-bounded wildcard stay untyped"),
    Shape("T18", """
        import java.util.List;
        import java.util.Optional;
        import java.util.concurrent.CompletableFuture;
        import java.util.stream.Stream;
        class Line {
          int qty() { return 1; } //@Line.qty
        }
        class Order {
          Line getLine() { return null; } //@Order.getLine
          List<Line> lines() { return null; } //@Order.lines
          Optional<Line> firstLine() { return null; } //@Order.firstLine
        }
        class Use {
          List<Order> orders;
          Optional<Order> maybe;
          CompletableFuture<Order> future;
          void run() { //@Use.run
            orders.stream().map(Order::getLine).findFirst().get().qty();
            orders.stream().flatMap(o -> o.lines().stream()).findFirst().get().qty();
            maybe.flatMap(Order::firstLine).get().qty();
            future.thenApply(o -> { return o.getLine(); }).join().qty();
          }
        }
        """, "Use.run", {
            "Order.getLine": "exact", "Order.lines": "exact", "Order.firstLine": "exact", "Line.qty": "exact",
        },
        note="BL-40: a method reference's return type, a lambda's result, the element of a flatMap result"),
    Shape("T18-unproven", """
        import java.util.List;
        import java.util.stream.Stream;
        class Line {
          int qty() { return 1; } //@Line.qty
        }
        class Order {
          Line pick() { return null; } //@Order.pick.0
          Line pick(int i) { return null; } //@Order.pick.1
          <T> T as() { return null; } //@Order.as
          Stream<Line> lines() { return null; } //@Order.lines
        }
        class Use {
          List<Order> orders;
          void run(Object other) { //@Use.run
            orders.stream().map(Order::pick).findFirst().get().qty();
            orders.stream().map(o -> other).findFirst().get().qty();
            orders.stream().flatMap(o -> { return o.lines(); }).findFirst().get().qty();
            orders.stream().flatMap(o -> o.lines().toList()).findFirst().get().qty();
          }
        }
        """, "Use.run", {"Order.lines": "exact"}, partial=True, absent=("Line.qty",),
        note="BL-40: two declared arities, an untyped result, a block body or another kind "
             "for flatMap stay untyped"),
    Shape("T19", """
        interface Shape {
          double area(); //@Shape.area
        }
        class Circle implements Shape {
          public double area() { return 1; } //@Circle.area
        }
        class Square implements Shape {
          public double area() { return 2; } //@Square.area
        }
        class Factory {
          Shape make() { //@Factory.make
            if (Math.random() > 0.5) {
              return new Circle();
            }
            Runnable task = () -> { return; };
            return (new Circle());
          }
        }
        class Use {
          Factory factory;
          void run() { //@Use.run
            factory.make().area();
            Shape local = factory.make();
            local.area();
          }
        }
        """, "Use.run", {"Factory.make": "exact", "Circle.area": "exact", "Shape.area": "possible"},
        note="BL-46: a call to a method whose own returns all create one class dispatches on it; "
             "a lambda's return is not the method's"),
    Shape("T19-kept", """
        interface Shape {
          double area(); //@Shape.area
        }
        class Circle implements Shape {
          public double area() { return 1; } //@Circle.area
        }
        class Square implements Shape {
          public double area() { return 2; } //@Square.area
        }
        class Factory {
          Shape mixed(boolean f) { if (f) return new Circle(); return new Square(); } //@Factory.mixed
          Shape passed(Shape s) { return s; } //@Factory.passed
          Shape overridden() { return new Circle(); } //@Factory.overridden
        }
        class SubFactory extends Factory {
          Shape overridden() { return new Square(); } //@SubFactory.overridden
        }
        class Use {
          Factory factory;
          void run(Shape s) { //@Use.run
            factory.mixed(true).area();
            factory.passed(s).area();
            factory.overridden().area();
          }
        }
        """, "Use.run", {
            "Factory.mixed": "exact", "Factory.passed": "exact",
            "Factory.overridden": "possible", "SubFactory.overridden": "possible",
            "Circle.area": "possible", "Square.area": "possible", "Shape.area": "possible",
        },
        note="BL-46: two created classes, a returned parameter and an overridden method keep "
             "the declared return type"),
    Shape("T20", """
        interface Exporter {
          void export(); //@Exporter.export
        }
        class Csv implements Exporter {
          public void export() {} //@Csv.export
        }
        class Json implements Exporter {
          public void export() {} //@Json.export
        }
        class Xml implements Exporter {
          public void export() {} //@Xml.export
        }
        class Use {
          Csv csv;
          Json json;
          void run(boolean flag) { //@Use.run
            Exporter chosen = flag ? csv : json;
            chosen.export();
          }
        }
        """, "Use.run", {"Csv.export": "possible", "Json.export": "possible"},
        absent=("Xml.export", "Exporter.export"),
        note="BL-46: a ternary of two source types dispatches on their union; the declared row "
             "is left out because its dispatch reaches Xml"),
    Shape("T20-single", """
        interface Exporter {
          void export(); //@Exporter.export
        }
        class Csv implements Exporter {
          public void export() {} //@Csv.export
        }
        class Json implements Exporter {
          public void export() {} //@Json.export
        }
        class Xml implements Exporter {
          public void export() {} //@Xml.export
        }
        class Use {
          Csv csv;
          Json json;
          void run(boolean flag) { //@Use.run
            Exporter created = flag ? new Csv() : null;
            created.export();
            Exporter same = (flag ? new Csv() : csv);
            same.export();
          }
        }
        """, "Use.run", {"Csv.export": "exact", "Exporter.export": "possible"},
        note="BL-46: a null branch gives nothing; a union of one class is exact under ADR-81"),
    Shape("T20-kept", """
        interface Exporter {
          void export(); //@Exporter.export
        }
        class Csv implements Exporter {
          public void export() {} //@Csv.export
        }
        class Json implements Exporter {
          public void export() {} //@Json.export
        }
        class Xml implements Exporter {
          public void export() {} //@Xml.export
        }
        class Use {
          Csv csv;
          Json json;
          Exporter any;
          void run(boolean flag, Exporter param) { //@Use.run
            Exporter wide = flag ? csv : param;
            wide.export();
            Exporter later = flag ? csv : json;
            later = any;
            later.export();
          }
        }
        """, "Use.run", {
            "Csv.export": "possible", "Json.export": "possible", "Xml.export": "possible",
            "Exporter.export": "possible",
        },
        note="BL-46: a branch of the declared type and a reassigned local keep the declared type"),
    Shape("T21", """
        interface Exporter {
          void export(); //@Exporter.export
        }
        class Csv implements Exporter {
          public void export() {} //@Csv.export
        }
        class Json implements Exporter {
          public void export() {} //@Json.export
        }
        class Xml implements Exporter {
          public void export() {} //@Xml.export
        }
        class Use {
          Csv csv;
          Json json;
          void run() {
            send(csv);
            send(new Csv());
          }
          private void send(Exporter exporter) { //@Use.send
            exporter.export();
            Runnable later = () -> exporter.export();
          }
        }
        """, "Use.send", {"Csv.export": "exact", "Exporter.export": "possible"},
        note="BL-46: every call site of a private method passes a Csv"),
    Shape("T21-mixed", """
        interface Exporter {
          void export(); //@Exporter.export
        }
        class Csv implements Exporter {
          public void export() {} //@Csv.export
        }
        class Json implements Exporter {
          public void export() {} //@Json.export
        }
        class Xml implements Exporter {
          public void export() {} //@Xml.export
        }
        class Use {
          Csv csv;
          Json json;
          void run() {
            send(csv);
            send(json);
          }
          private void send(Exporter exporter) { //@Use.send
            exporter.export();
          }
        }
        """, "Use.send", {
            "Csv.export": "possible", "Json.export": "possible", "Xml.export": "possible",
            "Exporter.export": "possible",
        },
        note="BL-46: sites passing two classes keep the declared type"),
    Shape("T21-public", """
        interface Exporter {
          void export(); //@Exporter.export
        }
        class Csv implements Exporter {
          public void export() {} //@Csv.export
        }
        class Json implements Exporter {
          public void export() {} //@Json.export
        }
        class Xml implements Exporter {
          public void export() {} //@Xml.export
        }
        class Use {
          Csv csv;
          Json json;
          void run() {
            send(csv);
          }
          void send(Exporter exporter) { //@Use.send
            exporter.export();
          }
        }
        """, "Use.send", {
            "Csv.export": "possible", "Json.export": "possible", "Xml.export": "possible",
            "Exporter.export": "possible",
        },
        note="BL-46: a package method can be called from code outside the sources"),
    Shape("T21-reference", """
        interface Exporter {
          void export(); //@Exporter.export
        }
        class Csv implements Exporter {
          public void export() {} //@Csv.export
        }
        class Json implements Exporter {
          public void export() {} //@Json.export
        }
        class Xml implements Exporter {
          public void export() {} //@Xml.export
        }
        class Use {
          Csv csv;
          Json json;
          void run() {
            send(csv);
            java.util.function.Consumer<Exporter> sink = this::send;
          }
          private void send(Exporter exporter) { //@Use.send
            exporter.export();
          }
        }
        """, "Use.send", {
            "Csv.export": "possible", "Json.export": "possible", "Xml.export": "possible",
            "Exporter.export": "possible",
        },
        note="BL-46: a method reference to the method is an unproven site"),
    Shape("T21-reassigned", """
        interface Exporter {
          void export(); //@Exporter.export
        }
        class Csv implements Exporter {
          public void export() {} //@Csv.export
        }
        class Json implements Exporter {
          public void export() {} //@Json.export
        }
        class Xml implements Exporter {
          public void export() {} //@Xml.export
        }
        class Use {
          Csv csv;
          Json json;
          void run() {
            send(csv);
          }
          private void send(Exporter exporter) { //@Use.send
            exporter = json;
            exporter.export();
          }
        }
        """, "Use.send", {
            "Csv.export": "possible", "Json.export": "possible", "Xml.export": "possible",
            "Exporter.export": "possible",
        },
        note="BL-46: a reassigned parameter keeps its declared type"),
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
        """, "Use.run", {"FnImpl.apply": "possible", "Fn.apply": "possible"},
        note="BL-30: a lambda somewhere implements Fn, so the named body is possible and the "
             "declared row of BL-28 joins it; no edge goes to the creating method"),
    Shape("F2-inherited-default", """
        interface Hook {
          void h(); //@Hook.h
          default void before() {} //@Hook.before
        }
        class Named implements Hook {
          public void h() {} //@Named.h
          public void before() {} //@Named.before
        }
        class Use {
          void reg(Hook x) { //@Use.reg
            x.before();
          }
          void register() {
            reg(() -> {});
          }
        }
        """, "Use.reg", {"Named.before": "possible", "Hook.before": "possible"},
        note="BC-09: the lambda passed to reg inherits Hook's default body, so Named's override "
             "is not the only target"),
    Shape("F2-target", """
        interface Fn {
          void apply(); //@Fn.apply
        }
        interface SubFn extends Fn {}
        interface Gn {
          void apply(); //@Gn.apply
        }
        class GnImpl implements Gn {
          public void apply() {} //@GnImpl.apply
        }
        class FnImpl implements Fn {
          public void apply() {} //@FnImpl.apply
        }
        class Registry {
          void register(SubFn fn) {} //@Registry.register
        }
        class Use {
          Fn f;
          Gn g;
          Registry registry;
          void run() { //@Use.run
            registry.register(this::noop);
            f.apply();
            g.apply();
          }
          void noop() {} //@Use.noop
        }
        """, "Use.run",
        {"Registry.register": "exact", "Use.noop": "exact", "FnImpl.apply": "possible", "Fn.apply": "possible", "GnImpl.apply": "exact", "Gn.apply": "possible"},
        note="BL-30: a method reference passed to a SubFn parameter implements Fn too; Gn, which "
             "no lambda implements, keeps its exact body and ADR-70 witness"),
    Shape("F2-contexts", """
        interface Fn {
          void apply(); //@Fn.apply
        }
        interface Gn {
          void apply(); //@Gn.apply
        }
        interface Hn {
          void apply(); //@Hn.apply
        }
        class FnImpl implements Fn {
          public void apply() {} //@FnImpl.apply
        }
        class GnImpl implements Gn {
          public void apply() {} //@GnImpl.apply
        }
        class HnImpl implements Hn {
          public void apply() {} //@HnImpl.apply
        }
        class Use {
          Fn f;
          Gn g;
          Hn h;
          Fn make() { return () -> {}; }
          void assign() { h = () -> {}; }
          void run() { //@Use.run
            Object cast = (Gn) () -> {};
            f.apply();
            g.apply();
            h.apply();
          }
        }
        """, "Use.run", {
            "FnImpl.apply": "possible", "Fn.apply": "possible",
            "GnImpl.apply": "possible", "Gn.apply": "possible",
            "HnImpl.apply": "possible", "Hn.apply": "possible",
        },
        note="BL-30: a returned lambda, a cast and an assignment write the target type"),
    Shape("F2-more-contexts", """
        interface Sn { void apply(); } //@Sn.apply
        interface En { void apply(); } //@En.apply
        interface Vn { void apply(); } //@Vn.apply
        interface An { void apply(); } //@An.apply
        interface Wn { void apply(); } //@Wn.apply
        class SnImpl implements Sn { public void apply() {} } //@SnImpl.apply
        class EnImpl implements En { public void apply() {} } //@EnImpl.apply
        class VnImpl implements Vn { public void apply() {} } //@VnImpl.apply
        class AnImpl implements An { public void apply() {} } //@AnImpl.apply
        class WnImpl implements Wn { public void apply() {} } //@WnImpl.apply
        class Base { Base(Sn s) {} }
        class Sub extends Base { Sub() { super(() -> {}); } }
        enum Op {
          A(() -> {});
          Op(En e) {}
        }
        class Pipe {
          static void of(Vn... values) {}
          static void use() { of(() -> {}, () -> {}); }
        }
        class Arrays {
          An[] fixed = { () -> {} };
          Wn pick(int i) {
            return switch (i) { case 0 -> () -> {}; default -> { yield new WnImpl(); } };
          }
        }
        class Use {
          Sn s; En e; Vn v; An a; Wn w;
          void run() { //@Use.run
            s.apply();
            e.apply();
            v.apply();
            a.apply();
            w.apply();
          }
        }
        """, "Use.run", {
            "SnImpl.apply": "possible", "Sn.apply": "possible",
            "EnImpl.apply": "possible", "En.apply": "possible",
            "VnImpl.apply": "possible", "Vn.apply": "possible",
            "AnImpl.apply": "possible", "An.apply": "possible",
            "WnImpl.apply": "possible", "Wn.apply": "possible",
        },
        note="BC-12: super(..), an enum constant, a varargs parameter, an array initializer and a "
             "switch arm write the target type of a lambda"),
    Shape("F2-created", """
        interface Fn {
          void apply(); //@Fn.apply
          default void log() {} //@Fn.log
        }
        interface Gn {
          void apply(); //@Gn.apply
        }
        class FnImpl implements Fn {
          public void apply() {} //@FnImpl.apply
          public void log() {} //@FnImpl.log
        }
        class Use {
          void run() { //@Use.run
            Fn fn = () -> {};
            fn.apply();
            fn.log();
            Gn gn = () -> {};
            gn.apply();
          }
        }
        """, "Use.run", {"Fn.log": "exact", "Gn.apply": "possible"},
        absent=("FnImpl.apply", "Fn.apply", "FnImpl.log"),
        note="BL-30: a variable holding a lambda reaches no named body of the method the lambda "
             "implements, and the declared row only when no named body could be reached from it; "
             "it inherits a default body exactly"),
    Shape("F3", """
        class Base {
          void hook() {} //@Base.hook
          void other() {} //@Base.other
        }
        class Use {
          void run(Base param) { //@Use.run
            Base b = new Base() { void hook() {} };
            b.hook();
            b.other();
            param.hook();
            param.other();
          }
        }
        """, "Use.run", {"Base.hook": "possible", "Base.other": "exact"},
        note="BL-30: the variable holding the anonymous instance reaches no named hook body and "
             "inherits other exactly; elsewhere, hook stays possible since the anonymous class "
             "overrides it, and other stays exact"),
    Shape("F3-kept", """
        class Base {
          void hook() {} //@Base.hook
        }
        class Sub extends Base {
          void hook() {} //@Sub.hook
        }
        class Use {
          void run() { //@Use.run
            Base b = new Base() { void other() {} };
            b.hook();
            Sub s = new Sub();
            s.hook();
          }
        }
        """, "Use.run", {"Base.hook": "exact", "Sub.hook": "exact"},
        note="BL-30: an anonymous class that does not override hook leaves the named bodies exact"),
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
    Shape("F6", """
        import java.util.List;
        import java.util.concurrent.CompletableFuture;
        import java.util.function.BiConsumer;
        import java.util.function.BiFunction;
        import java.util.function.BinaryOperator;
        import java.util.function.Consumer;
        import java.util.function.Function;
        import java.util.function.Predicate;
        import java.util.function.UnaryOperator;
        class Circle {
          boolean m01() { return true; } //@Circle.m01
          void m02() {} //@Circle.m02
          boolean m03() { return true; } //@Circle.m03
          Circle m04() { return this; } //@Circle.m04
          Circle m05() { return this; } //@Circle.m05
          Circle m06() { return this; } //@Circle.m06
          void m07() {} //@Circle.m07
          void m08() {} //@Circle.m08
          void m09() {} //@Circle.m09
          int m10() { return 1; } //@Circle.m10
          Circle self() { return this; } //@Circle.self
        }
        class Square {}
        class Taker {
          void take(Circle c) {} //@Taker.takeCircle
          void take(Square s) {} //@Taker.takeSquare
        }
        class Store {
          void each(Consumer<Circle> c) {} //@Store.each
          void both(BiConsumer<String, ? super Circle> c) {} //@Store.both
          <R> R apply(Function<Circle, R> f) { return null; } //@Store.apply
        }
        class Use {
          Store store;
          Taker taker;
          List<Circle> circles;
          CompletableFuture<Circle> future;
          Predicate<Circle> keep;
          void run() { //@Use.run
            Predicate<Circle> p = c -> c.m01();
            Consumer<Circle> k = c -> c.m02();
            keep = c -> c.m03();
            BiFunction<String, Circle, Circle> bf = (s, c) -> c.m04();
            UnaryOperator<Circle> u = c -> c.m05();
            BinaryOperator<Circle> b = (x, y) -> y.m06();
            store.each(c -> c.m07());
            store.both((s, c) -> c.m08());
            store.apply(c -> { return c.self(); }).m09();
            future.thenApply(c -> c.m10());
            circles.forEach(taker::take);
            Consumer<Square> squares = taker::take;
          }
        }
        """, "Use.run", {
            **{f"Circle.m{index:02d}": "exact" for index in range(1, 11)},
            "Circle.self": "exact", "Store.each": "exact", "Store.both": "exact", "Store.apply": "exact",
            "Taker.takeCircle": "exact", "Taker.takeSquare": "exact",
        },
        note="BL-37: JDK functional types type a lambda from a written variable, a source parameter "
             "or a future, the result a generic method infers, and an overloaded method reference"),
    Shape("F6-unproven", """
        import java.util.List;
        import java.util.function.Consumer;
        import java.util.function.Function;
        interface Round {}
        interface Flat {}
        class Circle implements Round, Flat {
          void area() {} //@Circle.area
        }
        class Square {
          void area() {} //@Square.area
        }
        class Taker {
          void take(Round r) {} //@Taker.takeRound
          void take(Flat f) {} //@Taker.takeFlat
        }
        class Store {
          <T> void open(Consumer<T> c) {} //@Store.open
          void any(Consumer<?> c) {} //@Store.any
          <R> R pick(Function<Circle, R> f) { return null; } //@Store.pick
        }
        class Use {
          List<Circle> circles;
          Taker taker;
          Store store;
          void run() { //@Use.run
            circles.forEach(taker::take);
            Consumer raw = c -> c.area();
            Consumer<?> wildcard = c -> c.area();
            store.open(c -> c.area());
            store.any(c -> c.area());
            store.pick(c -> { if (c == null) { return c; } return new Square(); }).area();
          }
        }
        """, "Use.run", {"Store.open": "exact", "Store.any": "exact", "Store.pick": "exact"},
        note="BL-37: two matching overloads, a raw or wildcard target, an unproven type variable "
             "and lambda returns of different types stay unresolved"),
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
    Shape("W9", """
        import java.util.List;
        import org.springframework.data.domain.Slice;
        import org.springframework.data.jpa.repository.JpaRepository;
        import org.springframework.data.repository.CrudRepository;
        import org.springframework.http.HttpEntity;
        import org.springframework.http.ResponseEntity;
        class Order {
          void m01() {} //@Order.m01
          void m02() {} //@Order.m02
          void m03() {} //@Order.m03
          void m04() {} //@Order.m04
          void m05() {} //@Order.m05
          void m06() {} //@Order.m06
          void m07() {} //@Order.m07
          void m08() {} //@Order.m08
        }
        class Special extends Order {
          void special() {} //@Special.special
        }
        class Line {
          void qty() {} //@Line.qty
        }
        interface BaseRepo<E> extends JpaRepository<E, Long> {}
        interface OrderRepo extends BaseRepo<Order> {}
        interface BothRepo extends CrudRepository<Order, Long>, JpaRepository<Order, Long> {}
        interface CrudOrders extends CrudRepository<Order, Long> {}
        interface LineRepo extends JpaRepository<Order, Long> {
          List<Line> findAll(); //@LineRepo.findAll
        }
        class Use {
          OrderRepo repo;
          BothRepo both;
          CrudOrders crud;
          LineRepo lines;
          Slice<Order> slice;
          HttpEntity<Order> entity;
          void run(Long id, Order o, Special s) { //@Use.run
            repo.findById(id).get().m01();
            repo.findAll().get(0).m02();
            both.findAll().get(0).m03();
            crud.findAll().forEach(x -> x.m04());
            repo.save(s).special();
            slice.getContent().get(0).m05();
            entity.getBody().m06();
            ResponseEntity.ok(o).getBody().m07();
            crud.findAllById(List.of(id)).iterator().next().m08();
            lines.findAll().get(0).qty();
          }
        }
        """, "Use.run", {
            "Order.m01": "exact", "Order.m02": "exact", "Order.m03": "exact", "Order.m04": "exact",
            "Special.special": "exact", "Order.m05": "exact", "Order.m06": "exact", "Order.m07": "exact",
            "Order.m08": "exact", "LineRepo.findAll": "possible", "Line.qty": "exact",
        },
        note="BL-43: Spring Data members through a generic source base, the most specific findAll, "
             "save of a typed argument, Page/Slice, HttpEntity and ResponseEntity; a source "
             "declaration wins over the table"),
    Shape("W9-unproven", """
        import org.springframework.data.jpa.repository.JpaRepository;
        import org.springframework.data.repository.CrudRepository;
        class Order {
          void ship() {} //@Order.ship
        }
        class Line {
          void qty() {} //@Line.qty
        }
        interface RawRepo extends JpaRepository {}
        interface LineRepo extends JpaRepository<Line, Long> {}
        interface MixedRepo extends CrudRepository<Order, Long>, LineRepo {}
        interface OrderRepo extends JpaRepository<Order, Long> {}
        class Use {
          RawRepo raw;
          MixedRepo mixed;
          OrderRepo repo;
          void run(Long id, Object unknown) { //@Use.run
            repo.getReferenceById(id).ship();
            repo.findOne(id).ship();
            raw.findAll().get(0).ship();
            raw.save(unknown).ship();
            mixed.findAll().iterator().next().ship();
            mixed.findAll().iterator().next().qty();
            repo.save(missing()).ship();
          }
        }
        """, "Use.run", {"Order.ship": "exact"},
        note="BL-43: a member absent from the table, a raw JpaRepository and two findAll returns "
             "of different elements stay untyped; save with an untyped argument returns T"),
    Shape("W10", """
        import org.springframework.beans.factory.BeanFactory;
        import org.springframework.context.ApplicationContext;
        class OrderService {
          void place() {} //@OrderService.place
          void cancel() {} //@OrderService.cancel
        }
        class Use {
          ApplicationContext ctx;
          BeanFactory factory;
          void run(Class<OrderService> type) { //@Use.run
            ctx.getBean(OrderService.class).place();
            factory.getBean("orders", OrderService.class).cancel();
            ctx.getBean(type).place();
          }
        }
        """, "Use.run", {"OrderService.place": "exact", "OrderService.cancel": "exact"},
        note="BL-44: a class literal or a written Class<T> types getBean"),
    Shape("W10-unproven", """
        import org.modelmapper.ModelMapper;
        import org.springframework.context.ApplicationContext;
        class OrderService {
          void place() {} //@OrderService.place
        }
        class Use {
          ApplicationContext ctx;
          ModelMapper mapper;
          void run(Object src, Class<?> any, Object name) { //@Use.run
            ctx.getBean("orders").place();
            ctx.getBean(any).place();
            ctx.getBean(name, OrderService.class).place();
            mapper.map(src, OrderService.class).place();
          }
        }
        """, "Use.run", {},
        note="BL-44: getBean(String), a wildcard Class, an untyped name and ModelMapper stay untyped"),
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
