"""Engine parity: the ``source`` engine against CodeQL on the alignment shapes.

Three fixture files hold every shape the ``source`` engine and the CodeQL
query were measured on: ``FIXTURE`` the alignment shapes (rows A1 to A10
and B1 to B6 of the engine-parity table in ``docs/spec-tech/indexing.md``),
``REVIEW_FIXTURE`` the shapes of the 2026-11-08 review (two-digit rows such
as A01 or C14, and E02 to E05), and ``COPILOT_FIXTURE`` those of the
Copilot backlog (E06 on). They tag lines as
``tests/test_call_graph_shapes.py`` does, with a trailing ``//@Tag``
comment: each caller's declaration line, each target's declaration line,
and each call line.

A case lists the rows CodeQL gives from one caller, after the join has
attributed anonymous and lambda callers to their creating method (ADR-62)
and dropped targets that are not indexed methods: ``(call line, callee,
confidence)``. The default test runs the symbol pass in a closed world, as
the ``source`` engine does, and expects the same rows. A case whose gap
an undelivered backlog item owns is a strict expected failure naming that
item; delivering the item removes its ``item`` value. A case whose
difference a decision record keeps lists the rows the ``source`` engine
gives instead, as its ``deviation``. The ``slow`` test
reads the raw rows of ``CallGraphSession.calls()`` and expects the same
rows from CodeQL, so the expectations stay measured rather than assumed.
A gap of the join both engines share names its item as ``codeql_item``.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
import re

import pytest

from systemlens.callgraph.engine import EngineRequest
from systemlens.callgraph.model import MethodCall
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.engines.codeql.database import codeql_executable
from systemlens.engines.codeql.engine import CodeQLEngine
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing.java_symbols import JavaSymbols

_PATH = "app/src/main/java/p/Shapes.java"
_REVIEW_PATH = "app/src/main/java/p/Review.java"
_COPILOT_PATH = "app/src/main/java/p/Copilot.java"
_TAG = re.compile(r"//@([\w.]+)\s*$")

FIXTURE = """\
package p;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import lombok.Data;
import org.acme.ExternalLabel;

interface Shape {
  double area(); //@Shape.area
}

class Circle implements Shape {
  public double area() { return 1; } //@Circle.area
}

class Square implements Shape {
  public double area() { return 2; } //@Square.area
}

// A1, A7: generic substitution.
class Box<T> {
  T value;
  T get() { return value; } //@Box.get
}

class CircleBox extends Box<Circle> {
  void run() { //@CircleBox.run
    value.area(); //@a1.field
  }
}

interface Repo<T> {
  T find(); //@Repo.find
}

interface CircleRepo extends Repo<Circle> {}

class Generics {
  Box<Circle> box;
  CircleRepo repo;
  List<Circle> circles;

  void box() { //@Generics.box
    box.get().area(); //@a1.box
  }

  void repo() { //@Generics.repo
    repo.find().area(); //@a1.repo
  }

  <T> T first(List<T> items) { //@Generics.first
    return items.get(0);
  }

  void useFirst() { //@Generics.useFirst
    first(circles).area(); //@a1.first
  }

  <T extends Shape> void bounded(T s) { //@Generics.bounded
    s.area(); //@a7
  }
}

// A2: JDK container accessors.
class Containers {
  List<Circle> circles;
  Optional<Circle> maybe;
  Map<String, Circle> byName;

  void listGet() { //@Containers.listGet
    circles.get(0).area(); //@a2.list
  }

  void optionalGet() { //@Containers.optionalGet
    maybe.get().area(); //@a2.optional
  }

  void mapGet() { //@Containers.mapGet
    byName.get("x").area(); //@a2.map
  }

  void findFirst() { //@Containers.findFirst
    circles.stream().findFirst().get().area(); //@a2.findFirst
  }
}

// A3, A4, A5: receiver forms.
class Receivers {
  List<Circle> circles;
  Circle a;
  Circle b;
  boolean flag;

  void varLoop() { //@Receivers.varLoop
    for (var c : circles) {
      c.area(); //@a3
    }
  }

  void ternary() { //@Receivers.ternary
    (flag ? a : b).area(); //@a4
  }

  void instanceOf(Object o) { //@Receivers.instanceOf
    if (o instanceof Circle c) {
      c.area(); //@a5.instanceof
    }
  }

  void switchPattern(Object o) { //@Receivers.switchPattern
    switch (o) {
      case Circle c -> c.area(); //@a5.switch
      default -> {}
    }
  }
}

// A6: qualified this.
class Outer {
  void helper() {} //@Outer.helper

  class Inner {
    void run() { //@Inner.run
      Outer.this.helper(); //@a6
    }
  }
}

// A8: unqualified call inside an anonymous class of an external type.
class Threads {
  void helper() {} //@Threads.helper

  void start() { //@Threads.start
    Thread t = new Thread() {
      public void run() {
        helper(); //@a8
      }
    };
    t.start();
  }
}

// A9: lambda passed to a source method.
interface Handler {
  void on(Circle c); //@Handler.on
}

class Events {
  void register(Handler h) {} //@Events.register

  void run() { //@Events.run
    register(c -> c.area()); //@a9
  }
}

// A10: method references.
class Sink {
  void handle(Circle c) {} //@Sink.handle
  void handle(Circle c, int n) {} //@Sink.handleTwo
  void only(Circle c) {} //@Sink.only
}

class References {
  List<Circle> circles;
  Sink sink;

  void overloaded() { //@References.overloaded
    circles.forEach(sink::handle); //@a10.overload
  }

  void oneArity() { //@References.oneArity
    circles.forEach(sink::only); //@a10.only
  }
}

// B1: a declared type narrowed by the type created once.
class Narrowing {
  private final Shape f = new Circle();

  void local() { //@Narrowing.local
    Shape s = new Circle();
    s.area(); //@b1.local
  }

  void field() { //@Narrowing.field
    f.area(); //@b1.field
  }
}

// B2: a lambda or an anonymous class implementing the receiver type.
interface Fn {
  void apply(); //@Fn.apply
}

class FnImpl implements Fn {
  public void apply() {} //@FnImpl.apply
}

class Hook {
  void hook() {} //@Hook.hook
}

class Lambdas {
  void lambda() { //@Lambdas.lambda
    Fn fn = () -> {};
    fn.apply(); //@b2.lambda
  }

  void anonymous() { //@Lambdas.anonymous
    Hook h = new Hook() { void hook() {} };
    h.hook(); //@b2.anonymous
  }
}

// B3: an external class extended in the sources.
class MyThread extends Thread {
  public void run() {} //@MyThread.run
}

class Spawner {
  void spawn() { //@Spawner.spawn
    Thread t = new MyThread();
    t.run(); //@b3
  }
}

// B4: two implementations of the declared type.
class Dispatch {
  Shape field;

  void run() { //@Dispatch.run
    field.area(); //@b4
  }
}

// B5: a generic implementation of a generic interface.
interface Handler2<T> {
  void handle(T t); //@Handler2.handle
}

class GenericHandler<T> implements Handler2<T> {
  public void handle(T t) {} //@GenericHandler.handle
}

class Handlers {
  Handler2<String> handler;

  void run() { //@Handlers.run
    handler.handle("x"); //@b5
  }
}

// B6: an implementor without a written body (ADR-81 rule 1).
interface Named {
  String name(); //@Named.name
}

class WrittenName implements Named {
  public String name() { return ""; } //@WrittenName.name
}

record GeneratedName(String name) implements Named {}

interface HasId {
  String getId(); //@HasId.getId
}

class WrittenId implements HasId {
  public String getId() { return ""; } //@WrittenId.getId
}

@Data
class LombokId implements HasId {
  private String id;
}

interface Labeled {
  String label(); //@Labeled.label
}

class WrittenLabel implements Labeled {
  public String label() { return ""; } //@WrittenLabel.label
}

class InheritedLabel extends ExternalLabel implements Labeled {}

interface Shown {
  String toString(); //@Shown.toString
}

class WrittenShown implements Shown {
  public String toString() { return ""; } //@WrittenShown.toString
}

class ObjectShown implements Shown {}

class Person {
  public String getName() { return ""; } //@Person.getName
}

@Data
class Employee extends Person {
  private String name;
}

interface Solo {
  String only(); //@Solo.only
}

class SoloImpl implements Solo {
  public String only() { return ""; } //@SoloImpl.only
}

class Implementors {
  void record(Named n) { //@Implementors.record
    n.name(); //@b6.record
  }

  void lombok(HasId h) { //@Implementors.lombok
    h.getId(); //@b6.lombok
  }

  void external(Labeled l) { //@Implementors.external
    l.label(); //@b6.external
  }

  void object(Shown s) { //@Implementors.object
    s.toString(); //@b6.object
  }

  void override(Person p) { //@Implementors.override
    p.getName(); //@b6.override
  }

  void complete(Solo s) { //@Implementors.complete
    s.only(); //@b6.complete
  }
}
"""

# The shapes of the 2026-11-08 review of internal flows (BACKLOG.md,
# Appendix A.1), under the review's case numbers, the generic return of
# Appendix B.4 (E02), and the dispatch shapes of BL-45 (E03) and BL-46
# (E04, E05). The file shares package ``p`` with ``FIXTURE`` and reuses its ``Shape``, ``Circle`` and ``Box`` types. Its Spring and
# ModelMapper imports let CodeQL type the library shapes from the jars it
# fetches; the ``source`` engine reads only the sources.
REVIEW_FIXTURE = """\
package p;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.function.BiFunction;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.function.Predicate;
import java.util.function.Supplier;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.modelmapper.ModelMapper;
import org.springframework.context.ApplicationContext;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.http.ResponseEntity;

class Line {
  public int qty() { return 1; } //@Line.qty
}

class Order {
  public String getName() { return ""; } //@Order.getName
  public Line getLine() { return new Line(); } //@Order.getLine
  public boolean ok() { return true; } //@Order.ok
  public void ship() {} //@Order.ship
}

interface OrderRepo extends JpaRepository<Order, Long> {
  Optional<Order> findByName(String name); //@OrderRepo.findByName
  Page<Order> findAll(Pageable pageable); //@OrderRepo.findAllPage
}

class OrderClient {
  ResponseEntity<Order> get() { return null; } //@OrderClient.get
}

class OrderService {
  void place() {} //@OrderService.place
  Shape shape() { return new Circle(); } //@OrderService.shape
  Order[] all() { return new Order[0]; } //@OrderService.all
}

// A: library and JDK generic signatures.
class LibCases {
  OrderRepo repo;
  OrderClient client;
  Map<String, Order> m;
  Map<String, List<Order>> nested;
  List<Order> orders;
  CompletableFuture<Order> f;
  ApplicationContext ctx;
  ModelMapper mapper;
  Order order;
  Order other;

  void a01(Long id) { //@A01
    repo.findById(id).orElseThrow().getName(); //@a01
  }

  void a02(Long id) { //@A02
    repo.findById(id).get().getName(); //@a02
  }

  void a04(Long id) { //@A04
    repo.findById(id).ifPresent(o -> o.ship()); //@a04
  }

  void a05() { //@A05
    repo.findAll().get(0).ship(); //@a05
  }

  void a06() { //@A06
    repo.findAll().forEach(o -> o.ship()); //@a06
  }

  void a07() { //@A07
    repo.findAll().stream().map(o -> o.getName()); //@a07
  }

  void a08() { //@A08
    repo.save(order).ship(); //@a08
  }

  void a09(List<Long> ids) { //@A09
    repo.findAllById(ids).get(0).ship(); //@a09
  }

  void a10() { //@A10
    repo.findByName("x").orElseThrow().ship(); //@a10
  }

  void a11(Pageable p) { //@A11
    repo.findAll(p).getContent().get(0).ship(); //@a11
  }

  void a12() { //@A12
    ResponseEntity<Order> r = client.get(); //@a12.get
    r.getBody().ship(); //@a12
  }

  void a13() { //@A13
    ResponseEntity.ok(order).getBody().ship(); //@a13
  }

  void a15(String k) { //@A15
    m.computeIfAbsent(k, x -> new Order()).ship(); //@a15
  }

  void a16(String k) { //@A16
    m.getOrDefault(k, other).ship(); //@a16
  }

  void a18() { //@A18
    orders.stream().map(Order::getLine).toList().get(0).qty(); //@a18
  }

  void a19() { //@A19
    orders.stream().collect(Collectors.toList()).get(0).ship(); //@a19
  }

  void a24() { //@A24
    Optional.ofNullable(order).map(Order::getLine).orElse(null).qty(); //@a24
  }

  void a25() { //@A25
    Optional.of(order).orElseGet(() -> other).ship(); //@a25
  }

  void a26() { //@A26
    f.join().ship(); //@a26
  }

  void a27() { //@A27
    f.thenApply(o -> o.getName()); //@a27
  }

  void a28() { //@A28
    List.of(order, other).get(0).ship(); //@a28
  }

  void a29() { //@A29
    Arrays.asList(order, other).get(0).ship(); //@a29
  }

  void a30() { //@A30
    Collections.unmodifiableList(orders).get(0).ship(); //@a30
  }

  void a31() { //@A31
    new ArrayList<>(orders).get(0).ship(); //@a31
  }

  void a32() { //@A32
    ctx.getBean(OrderService.class).place(); //@a32
  }

  void a33() { //@A33
    Objects.requireNonNull(order).ship(); //@a33
  }

  void a34(Object src) { //@A34
    mapper.map(src, Order.class).ship(); //@a34
  }

  void a38() { //@A38
    var list = repo.findAll();
    list.get(0).ship(); //@a38
  }

  void a39() { //@A39
    for (var e : m.entrySet()) {
      e.getValue().ship(); //@a39
    }
  }

  void a41() { //@A41
    orders.stream().map(o -> o.getLine()).forEach(l -> l.qty()); //@a41
  }

  void a42() { //@A42
    client.get().getBody().ship(); //@a42
  }

  void a43() { //@A43
    nested.get("x").get(0).ship(); //@a43
  }

  void a44() { //@A44
    var opt = Optional.of(order);
    opt.get().ship(); //@a44
  }

  void a45() { //@A45
    Stream.of(order, other).findFirst().get().ship(); //@a45
  }
}

// B: typing forms.
enum Status {
  ACTIVE, DONE;

  String label() { return name(); } //@Status.label
}

record Money(Order o) {
  Money { //@B23
    o.ship(); //@b23
  }
}

class FormCases {
  OrderService service;
  Supplier<Order> supplier;
  Function<String, Order> function;
  BiFunction<String, Integer, Order> bi;
  Order order;

  void b10() { //@B10
    service.shape().area(); //@b10
  }

  void b14(String s) { //@B14
    Status.valueOf(s).label(); //@b14
  }

  void b15() { //@B15
    Status.values()[0].label(); //@b15
  }

  void b26() { //@B26
    supplier.get().ship(); //@b26
  }

  void b27(String k) { //@B27
    function.apply(k).ship(); //@b27
  }

  void b28() { //@B28
    bi.apply("x", 1).ship(); //@b28
  }

  void b29() { //@B29
    Predicate<Order> p = o -> o.ok(); //@b29
    p.test(order);
  }

  void b30() { //@B30
    Consumer<Order> c = o -> o.ship(); //@b30
    c.accept(order);
  }

  void b39() { //@B39
    Shape s = service.shape(); //@b39.shape
    s.area(); //@b39
  }

  void b44() { //@B44
    Order[] arr = service.all(); //@b44.all
    for (var o : arr) {
      o.ship(); //@b44
    }
  }

  void b46() { //@B46
    Function<Order, Line> g = Order::getLine; //@b46
    g.apply(order).qty(); //@b46.apply
  }
}

// C: generic JDK types written in the sources.
class Catalog {
  List<Order> list() { return null; } //@Catalog.list
  Optional<Order> maybe() { return null; } //@Catalog.maybe
  Map<String, Order> index() { return null; } //@Catalog.index
  static List<Order> all() { return null; } //@Catalog.all
  void each(Consumer<Order> c) {} //@Catalog.each
  <R> R apply(Function<Order, R> f) { return null; } //@Catalog.apply
}

class SourceGenericCases {
  Catalog catalog;
  List<Order> orders;
  Order order;
  boolean flag;

  void c01() { //@C01
    catalog.list().get(0).ship(); //@c01
  }

  void c02() { //@C02
    catalog.maybe().get().ship(); //@c02
  }

  void c03() { //@C03
    catalog.index().get("x").ship(); //@c03
  }

  void c04() { //@C04
    Catalog.all().get(0).ship(); //@c04
  }

  void c05() { //@C05
    catalog.list().forEach(o -> o.ship()); //@c05
  }

  void c06() { //@C06
    for (var o : catalog.list()) { //@c06.list
      o.ship(); //@c06
    }
  }

  void c07() { //@C07
    var list = orders;
    list.get(0).ship(); //@c07
  }

  void c08() { //@C08
    var s = orders.stream();
    s.findFirst().get().ship(); //@c08
  }

  void c09() { //@C09
    catalog.each(o -> o.ship()); //@c09
  }

  void c10() { //@C10
    catalog.apply(o -> o.getLine()).qty(); //@c10
  }

  void c11() { //@C11
    (flag ? order : null).ship(); //@c11
  }

  void c12() { //@C12
    catalog.list().stream().filter(o -> o.ok()).findFirst().get().ship(); //@c12
  }

  void c14() { //@C14
    var l = catalog.list(); //@c14.list
    l.get(0).ship(); //@c14
  }
}

// D: overloads and cascades.
class Taker {
  void take(Order o) {} //@Taker.takeOrder
  void take(Line l) {} //@Taker.takeLine
}

class CascadeCases {
  Catalog catalog;
  Taker taker;
  List<Order> orders;

  void d01() { //@D01
    taker.take(catalog.list().get(0)); //@d01
  }

  void d04() { //@D04
    new ArrayList<Order>(orders).get(0).ship(); //@d04
  }

  void d05() { //@D05
    catalog.list().forEach(taker::take); //@d05
  }

  void d06() { //@D06
    orders.forEach(taker::take); //@d06
  }

  void d07(Comparator<Order> cmp) { //@D07
    taker.take(Collections.max(orders, cmp)); //@d07
  }
}

// E02: a generic return declared on an interface and on an abstract class.
interface Wrapper<T> {
  Box<T> wrap(); //@Wrapper.wrap
}

abstract class BaseWrapper<T> implements Wrapper<T> {
  public abstract Box<T> wrap(); //@BaseWrapper.wrap
}

abstract class OrderWrapper extends BaseWrapper<Order> {}

class OrderBoxes extends OrderWrapper {
  public Box<Order> wrap() { return null; } //@OrderBoxes.wrap
}

class WrapperCases {
  OrderWrapper wrapper;

  void e02() { //@E02
    wrapper.wrap().get().ship(); //@e02
  }
}

// E03: implementors whose type argument contradicts the receiver's (BL-45).
interface Codec<T> {
  void encode(T value); //@Codec.encode
}

class TextCodec implements Codec<String> {
  public void encode(String value) {} //@TextCodec.encode
}

class BinaryCodec implements Codec<byte[]> {
  public void encode(byte[] value) {} //@BinaryCodec.encode
}

class CodecCases {
  Codec<String> codec;

  void e03() { //@E03
    codec.encode("x"); //@e03
  }
}

// E04, E05: a ternary of two source types, and a private helper's call
// context (BL-46).
interface Printer {
  void print(); //@Printer.print
}

class CsvPrinter implements Printer {
  public void print() {} //@CsvPrinter.print
}

class JsonPrinter implements Printer {
  public void print() {} //@JsonPrinter.print
}

class XmlPrinter implements Printer {
  public void print() {} //@XmlPrinter.print
}

class PrinterCases {
  CsvPrinter csv;
  JsonPrinter json;

  void e04(boolean flag) { //@E04
    Printer chosen = flag ? csv : json;
    chosen.print(); //@e04
  }

  void e05() {
    printWith(csv);
  }

  private void printWith(Printer printer) { //@E05
    printer.print(); //@e05
  }
}
"""

# The Copilot backlog (backlog-copilot.md): one section per BC item.
COPILOT_FIXTURE = """\
package p;

// E06: the implicit supertypes of an enum and a record (BC-06).
enum Tint { RED }

record Point(int x) {}

class Implicit {
  void o(Object x) {} //@Implicit.oObject
  void o(Enum<?> x) {} //@Implicit.oEnum
  void r(Object x) {} //@Implicit.rObject
  void r(Record x) {} //@Implicit.rRecord
  void c(Comparable<?> x) {} //@Implicit.cComparable
  void c(Runnable x) {} //@Implicit.cRunnable

  void e06() { //@E06
    o(Tint.RED); //@e06.enum
    r(new Point(1)); //@e06.record
    c(Tint.RED); //@e06.comparable
  }
}
"""

_SOURCES = {_PATH: FIXTURE, _REVIEW_PATH: REVIEW_FIXTURE, _COPILOT_PATH: COPILOT_FIXTURE}

Row = tuple[str, str, str]


@dataclass(frozen=True)
class Case:
    """The rows CodeQL gives from one caller: (call line tag, callee tag, confidence)."""

    id: str
    caller: str
    rows: frozenset[Row]
    # The backlog item that owns the gap; ``None`` once the symbol pass agrees.
    item: str | None
    # The rows the ``source`` engine gives instead, when a decision record
    # keeps the difference; ``None`` when the engines agree.
    deviation: frozenset[Row] | None = None
    # The backlog item that owns a gap of the join both engines share, such
    # as a caller missing from the method inventory: the CodeQL test then
    # fails too, as a strict expected failure naming it.
    codeql_item: str | None = None


def _case(
    id: str, caller: str, item: str | None, *rows: Row, deviation: Iterable[Row] | None = None,
    codeql_item: str | None = None,
) -> Case:
    return Case(
        id, caller, frozenset(rows), item, None if deviation is None else frozenset(deviation), codeql_item,
    )


CASES: list[Case] = [
    _case("A1-box", "Generics.box", None,
          ("a1.box", "Box.get", "exact"), ("a1.box", "Circle.area", "exact")),
    _case("A1-repo", "Generics.repo", None,
          ("a1.repo", "Repo.find", "possible"), ("a1.repo", "Circle.area", "exact")),
    _case("A1-field", "CircleBox.run", None,
          ("a1.field", "Circle.area", "exact")),
    _case("A1-generic-method", "Generics.useFirst", None,
          ("a1.first", "Generics.first", "exact"), ("a1.first", "Circle.area", "exact")),
    _case("A2-list", "Containers.listGet", None,
          ("a2.list", "Circle.area", "exact")),
    _case("A2-optional", "Containers.optionalGet", None,
          ("a2.optional", "Circle.area", "exact")),
    _case("A2-map", "Containers.mapGet", None,
          ("a2.map", "Circle.area", "exact")),
    _case("A2-find-first", "Containers.findFirst", None,
          ("a2.findFirst", "Circle.area", "exact")),
    _case("A3", "Receivers.varLoop", None,
          ("a3", "Circle.area", "exact")),
    _case("A4", "Receivers.ternary", None,
          ("a4", "Circle.area", "exact")),
    _case("A5-instanceof", "Receivers.instanceOf", None,
          ("a5.instanceof", "Circle.area", "exact")),
    _case("A5-switch", "Receivers.switchPattern", None,
          ("a5.switch", "Circle.area", "exact")),
    _case("A6", "Inner.run", None,
          ("a6", "Outer.helper", "exact")),
    _case("A7", "Generics.bounded", None,
          ("a7", "Circle.area", "possible"), ("a7", "Square.area", "possible"),
          ("a7", "Shape.area", "possible")),
    _case("A8", "Threads.start", None,
          ("a8", "Threads.helper", "exact")),
    _case("A9", "Events.run", None,
          ("a9", "Events.register", "exact"), ("a9", "Circle.area", "exact")),
    _case("A10-overload", "References.overloaded", None,
          ("a10.overload", "Sink.handle", "exact")),
    _case("A10-one-arity", "References.oneArity", None,
          ("a10.only", "Sink.only", "exact")),
    _case("B1-local", "Narrowing.local", None,
          ("b1.local", "Circle.area", "exact"), ("b1.local", "Shape.area", "possible")),
    _case("B1-field", "Narrowing.field", None,
          ("b1.field", "Circle.area", "exact"), ("b1.field", "Shape.area", "possible")),
    # ADR-72: a rebuild from persisted edges would dispatch the declared
    # row to ``FnImpl.apply``, which the variable never holds; the row is
    # left out, as ADR-70 leaves out a declared row that opens a route.
    _case("B2-lambda", "Lambdas.lambda", None,
          ("b2.lambda", "Fn.apply", "possible"), deviation=()),
    _case("B2-anonymous", "Lambdas.anonymous", None),
    _case("B3", "Spawner.spawn", None,
          ("b3", "MyThread.run", "exact")),
    _case("B4", "Dispatch.run", None,
          ("b4", "Circle.area", "possible"), ("b4", "Square.area", "possible"),
          ("b4", "Shape.area", "possible")),
    _case("B5", "Handlers.run", None,
          ("b5", "GenericHandler.handle", "exact"), ("b5", "Handler2.handle", "possible")),
    _case("B6-record", "Implementors.record", None,
          ("b6.record", "WrittenName.name", "possible"), ("b6.record", "Named.name", "possible")),
    _case("B6-lombok", "Implementors.lombok", None,
          ("b6.lombok", "WrittenId.getId", "possible"), ("b6.lombok", "HasId.getId", "possible")),
    _case("B6-external", "Implementors.external", None,
          ("b6.external", "WrittenLabel.label", "possible"),
          ("b6.external", "Labeled.label", "possible")),
    _case("B6-object", "Implementors.object", None,
          ("b6.object", "WrittenShown.toString", "possible"),
          ("b6.object", "Shown.toString", "possible")),
    _case("B6-override", "Implementors.override", None,
          ("b6.override", "Person.getName", "possible")),
    _case("B6-complete", "Implementors.complete", None,
          ("b6.complete", "SoloImpl.only", "exact"), ("b6.complete", "Solo.only", "possible")),
    # The 2026-11-08 review (BACKLOG.md, Appendix A.1): CodeQL's measured
    # rows; ``item`` names every owner of the gap, in the order of the table.
    _case("A01", "A01", None,
          ("a01", "Order.getName", "exact")),
    _case("A02", "A02", None,
          ("a02", "Order.getName", "exact")),
    _case("A04", "A04", None,
          ("a04", "Order.ship", "exact")),
    _case("A05", "A05", None,
          ("a05", "Order.ship", "exact")),
    _case("A06", "A06", None,
          ("a06", "Order.ship", "exact")),
    _case("A07", "A07", None,
          ("a07", "Order.getName", "exact")),
    _case("A08", "A08", None,
          ("a08", "Order.ship", "exact")),
    _case("A09", "A09", None,
          ("a09", "Order.ship", "exact")),
    _case("A10", "A10", None,
          ("a10", "OrderRepo.findByName", "possible"), ("a10", "Order.ship", "exact")),
    _case("A11", "A11", None,
          ("a11", "OrderRepo.findAllPage", "possible"), ("a11", "Order.ship", "exact")),
    _case("A12", "A12", None,
          ("a12.get", "OrderClient.get", "exact"), ("a12", "Order.ship", "exact")),
    _case("A13", "A13", None,
          ("a13", "Order.ship", "exact")),
    _case("A15", "A15", None,
          ("a15", "Order.ship", "exact")),
    _case("A16", "A16", None,
          ("a16", "Order.ship", "exact")),
    _case("A18", "A18", None,
          ("a18", "Order.getLine", "exact"), ("a18", "Line.qty", "exact")),
    _case("A19", "A19", None,
          ("a19", "Order.ship", "exact")),
    _case("A24", "A24", None,
          ("a24", "Order.getLine", "exact"), ("a24", "Line.qty", "exact")),
    _case("A25", "A25", None,
          ("a25", "Order.ship", "exact")),
    _case("A26", "A26", None,
          ("a26", "Order.ship", "exact")),
    _case("A27", "A27", None,
          ("a27", "Order.getName", "exact")),
    _case("A28", "A28", None,
          ("a28", "Order.ship", "exact")),
    _case("A29", "A29", None,
          ("a29", "Order.ship", "exact")),
    _case("A30", "A30", None,
          ("a30", "Order.ship", "exact")),
    _case("A31", "A31", None,
          ("a31", "Order.ship", "exact")),
    _case("A32", "A32", None,
          ("a32", "OrderService.place", "exact")),
    _case("A33", "A33", None,
          ("a33", "Order.ship", "exact")),
    # ADR-90: ModelMapper is not in the Spring signature table, so the
    # source engine leaves ``mapper.map(src, Order.class)`` untyped where
    # CodeQL types it from the jar it downloads.
    _case("A34", "A34", None,
          ("a34", "Order.ship", "exact"), deviation=()),
    _case("A38", "A38", None,
          ("a38", "Order.ship", "exact")),
    _case("A39", "A39", None,
          ("a39", "Order.ship", "exact")),
    _case("A41", "A41", None,
          ("a41", "Order.getLine", "exact"), ("a41", "Line.qty", "exact")),
    _case("A42", "A42", None,
          ("a42", "OrderClient.get", "exact"), ("a42", "Order.ship", "exact")),
    _case("A43", "A43", None,
          ("a43", "Order.ship", "exact")),
    _case("A44", "A44", None,
          ("a44", "Order.ship", "exact")),
    _case("A45", "A45", None,
          ("a45", "Order.ship", "exact")),
    _case("B10", "B10", None,
          ("b10", "OrderService.shape", "exact"), ("b10", "Circle.area", "exact"), ("b10", "Shape.area", "possible")),
    _case("B14", "B14", None,
          ("b14", "Status.label", "exact")),
    _case("B15", "B15", None,
          ("b15", "Status.label", "exact")),
    _case("B23", "B23", None,
          ("b23", "Order.ship", "exact")),
    _case("B26", "B26", None,
          ("b26", "Order.ship", "exact")),
    _case("B27", "B27", None,
          ("b27", "Order.ship", "exact")),
    _case("B28", "B28", None,
          ("b28", "Order.ship", "exact")),
    _case("B29", "B29", None,
          ("b29", "Order.ok", "exact")),
    _case("B30", "B30", None,
          ("b30", "Order.ship", "exact")),
    _case("B39", "B39", None,
          ("b39.shape", "OrderService.shape", "exact"), ("b39", "Circle.area", "exact"), ("b39", "Shape.area", "possible")),
    _case("B44", "B44", None,
          ("b44.all", "OrderService.all", "exact"), ("b44", "Order.ship", "exact")),
    _case("B46", "B46", None,
          ("b46", "Order.getLine", "exact"), ("b46.apply", "Line.qty", "exact")),
    _case("C01", "C01", None,
          ("c01", "Catalog.list", "exact"), ("c01", "Order.ship", "exact")),
    _case("C02", "C02", None,
          ("c02", "Catalog.maybe", "exact"), ("c02", "Order.ship", "exact")),
    _case("C03", "C03", None,
          ("c03", "Catalog.index", "exact"), ("c03", "Order.ship", "exact")),
    _case("C04", "C04", None,
          ("c04", "Catalog.all", "exact"), ("c04", "Order.ship", "exact")),
    _case("C05", "C05", None,
          ("c05", "Catalog.list", "exact"), ("c05", "Order.ship", "exact")),
    _case("C06", "C06", None,
          ("c06.list", "Catalog.list", "exact"), ("c06", "Order.ship", "exact")),
    _case("C07", "C07", None,
          ("c07", "Order.ship", "exact")),
    _case("C08", "C08", None,
          ("c08", "Order.ship", "exact")),
    _case("C09", "C09", None,
          ("c09", "Catalog.each", "exact"), ("c09", "Order.ship", "exact")),
    _case("C10", "C10", None,
          ("c10", "Catalog.apply", "exact"), ("c10", "Order.getLine", "exact"), ("c10", "Line.qty", "exact")),
    _case("C11", "C11", None,
          ("c11", "Order.ship", "exact")),
    _case("C12", "C12", None,
          ("c12", "Catalog.list", "exact"), ("c12", "Order.ok", "exact"), ("c12", "Order.ship", "exact")),
    _case("C14", "C14", None,
          ("c14.list", "Catalog.list", "exact"), ("c14", "Order.ship", "exact")),
    _case("D01", "D01", None,
          ("d01", "Catalog.list", "exact"), ("d01", "Taker.takeOrder", "exact")),
    _case("D04", "D04", None,
          ("d04", "Order.ship", "exact")),
    _case("D05", "D05", None,
          ("d05", "Catalog.list", "exact"), ("d05", "Taker.takeOrder", "exact")),
    _case("D06", "D06", None,
          ("d06", "Taker.takeOrder", "exact")),
    # ADR-87: the pass does not type ``Collections.max``, so it keeps both
    # overloads ``possible`` where CodeQL selects ``take(Order)``.
    _case("D07", "D07", None,
          ("d07", "Taker.takeOrder", "exact"),
          deviation=[("d07", "Taker.takeOrder", "possible"), ("d07", "Taker.takeLine", "possible")]),
    _case("E02", "E02", None,
          ("e02", "OrderBoxes.wrap", "exact"), ("e02", "Box.get", "exact"), ("e02", "Order.ship", "exact")),
    _case("E03", "E03", None,
          ("e03", "TextCodec.encode", "exact"), ("e03", "Codec.encode", "possible")),
    # ADR-89: CodeQL also reports the declared row Printer.print, which a
    # rebuild from persisted edges would dispatch to XmlPrinter; the source
    # engine leaves it out (ADR-70, BL-28 amendment).
    _case("E04", "E04", None,
          ("e04", "CsvPrinter.print", "possible"), ("e04", "JsonPrinter.print", "possible"),
          ("e04", "Printer.print", "possible"),
          deviation=[("e04", "CsvPrinter.print", "possible"), ("e04", "JsonPrinter.print", "possible")]),
    _case("E05", "E05", None,
          ("e05", "CsvPrinter.print", "exact"), ("e05", "Printer.print", "possible")),
    _case("E06", "E06", None,
          ("e06.enum", "Implicit.oEnum", "exact"), ("e06.record", "Implicit.rRecord", "exact"),
          ("e06.comparable", "Implicit.cComparable", "exact")),
]


@dataclass(frozen=True)
class _Project:
    root: Path
    methods: list[IntegrationMethod]
    # (path, line) -> tag, and tag -> line.
    tags: dict[tuple[str, int], str]
    lines: dict[str, int]


def _project(root: Path) -> _Project:
    tags: dict[tuple[str, int], str] = {}
    lines: dict[str, int] = {}
    for path, text in _SOURCES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            match = _TAG.search(line)
            if match:
                assert match.group(1) not in lines, f"duplicate tag {match.group(1)}"
                tags[(path, number)] = match.group(1)
                lines[match.group(1)] = number
    module = DiscoveredModule(
        name="app", path=root / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(root, [], list(_SOURCES), [module])
    return _Project(root, methods, tags, lines)


def _label(project: _Project, path: str, line: int) -> str:
    return project.tags.get((path, line), f"{path}:{line}")


def _caller_of(project: _Project, call: MethodCall) -> IntegrationMethod | None:
    """The smallest indexed method holding the call line (ADR-62 join)."""
    holding = [
        method for method in project.methods
        if method.path == call.caller_path and method.start_line <= call.call_line <= method.end_line
    ]
    return min(holding, key=lambda method: method.end_line - method.start_line, default=None)


def _rows(project: _Project, calls: Iterable[MethodCall]) -> dict[str, set[Row]]:
    """Rows by caller tag, keeping only callees that are indexed methods."""
    indexed = {(method.path, method.start_line) for method in project.methods}
    rows: dict[str, set[Row]] = {}
    for call in calls:
        if (call.callee_path, call.callee_line) not in indexed:
            continue
        caller = _caller_of(project, call)
        if caller is None:
            continue
        rows.setdefault(_label(project, caller.path, caller.start_line), set()).add((
            _label(project, call.caller_path, call.call_line),
            _label(project, call.callee_path, call.callee_line),
            call.dispatch_confidence,
        ))
    return rows


def _check(project: _Project, rows: dict[str, set[Row]], case: Case, expected: frozenset[Row] | None = None) -> None:
    expected = case.rows if expected is None else expected
    for site, callee, _ in case.rows | expected:
        assert site in project.lines and callee in project.lines, f"{case.id}: untagged row"
    assert case.caller in project.lines, f"{case.id}: no //@{case.caller} line"
    got = rows.get(case.caller, set())
    assert got == set(expected), (
        f"{case.id}: missing {sorted(set(expected) - got)}, extra {sorted(got - set(expected))}"
    )


@pytest.fixture(scope="module")
def symbol_rows(tmp_path_factory: pytest.TempPathFactory) -> tuple[_Project, dict[str, set[Row]]]:
    project = _project(tmp_path_factory.mktemp("parity-source"))
    symbols = JavaSymbols(project.root, project.methods, list(_SOURCES))
    return project, _rows(project, symbols.fallback_calls(set(), closed_world=True))


def _source_param(case: Case) -> object:
    marks = [pytest.mark.xfail(strict=True, reason=case.item)] if case.item else []
    return pytest.param(case, id=case.id, marks=marks)


@pytest.mark.parametrize("case", [_source_param(case) for case in CASES])
def test_source_engine_matches_codeql(
    symbol_rows: tuple[_Project, dict[str, set[Row]]], case: Case,
) -> None:
    _check(*symbol_rows, case, case.deviation)


@pytest.fixture(scope="module")
def codeql_rows(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[_Project, dict[str, set[Row]]]]:
    if codeql_executable() is None:
        pytest.skip("Local CodeQL is required")
    project = _project(tmp_path_factory.mktemp("parity-codeql"))
    request = EngineRequest(repo_root=project.root, allowed_paths=list(_SOURCES), timeout_seconds=600)
    with CodeQLEngine(threads=2).open(request) as session:
        calls = session.calls()
    yield project, _rows(project, calls)


def _codeql_param(case: Case) -> object:
    marks = [pytest.mark.xfail(strict=True, reason=case.codeql_item)] if case.codeql_item else []
    return pytest.param(case, id=case.id, marks=marks)


@pytest.mark.slow
@pytest.mark.parametrize("case", [_codeql_param(case) for case in CASES])
def test_codeql_gives_the_parity_rows(
    codeql_rows: tuple[_Project, dict[str, set[Row]]], case: Case,
) -> None:
    _check(*codeql_rows, case)


def test_every_parity_row_is_documented() -> None:
    spec = Path(__file__).resolve().parents[1] / "docs/spec-tech/indexing.md"
    text = spec.read_text(encoding="utf-8")
    section = text[text.index("### Engine parity"):text.index("## Build layout")]
    documented = dict(re.findall(r"^\| ([A-E]\d+) \|.*?\|.*?\| (.*?) \|$", section, re.MULTILINE))
    rows = {case.id.split("-", 1)[0] for case in CASES}
    assert set(documented) == rows, f"undocumented: {sorted(rows - set(documented))}"
    for case in CASES:
        for item in (case.item, case.codeql_item):
            if item:
                assert item in documented[case.id.split("-", 1)[0]], (
                    f"{case.id}: {item} missing from the parity table"
                )
        if case.deviation is not None:
            assert case.id in documented[case.id.split("-", 1)[0]], (
                f"{case.id}: its deviation is missing from the parity table"
            )

