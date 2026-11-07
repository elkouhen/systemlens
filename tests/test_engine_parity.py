"""Engine parity: the ``source`` engine against CodeQL on the alignment shapes.

One fixture holds every shape the ``source`` engine and the CodeQL query
were measured on (rows A1 to A10 and B1 to B5 of the engine-parity table in
``docs/spec-tech/indexing.md``). It tags lines as
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
_TAG = re.compile(r"//@([\w.]+)\s*$")

FIXTURE = """\
package p;

import java.util.List;
import java.util.Map;
import java.util.Optional;

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
"""

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


def _case(id: str, caller: str, item: str | None, *rows: Row, deviation: Iterable[Row] | None = None) -> Case:
    return Case(id, caller, frozenset(rows), item, None if deviation is None else frozenset(deviation))


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
]


@dataclass(frozen=True)
class _Project:
    root: Path
    methods: list[IntegrationMethod]
    # (path, line) -> tag, and tag -> line.
    tags: dict[tuple[str, int], str]
    lines: dict[str, int]


def _project(root: Path) -> _Project:
    (root / _PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / _PATH).write_text(FIXTURE, encoding="utf-8")
    tags: dict[tuple[str, int], str] = {}
    lines: dict[str, int] = {}
    for number, line in enumerate(FIXTURE.splitlines(), start=1):
        match = _TAG.search(line)
        if match:
            assert match.group(1) not in lines, f"duplicate tag {match.group(1)}"
            tags[(_PATH, number)] = match.group(1)
            lines[match.group(1)] = number
    module = DiscoveredModule(
        name="app", path=root / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(root, [], [_PATH], [module])
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
    symbols = JavaSymbols(project.root, project.methods, [_PATH])
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
    request = EngineRequest(repo_root=project.root, allowed_paths=[_PATH], timeout_seconds=600)
    with CodeQLEngine(threads=2).open(request) as session:
        calls = session.calls()
    yield project, _rows(project, calls)


@pytest.mark.slow
@pytest.mark.parametrize("case", [pytest.param(case, id=case.id) for case in CASES])
def test_codeql_gives_the_parity_rows(
    codeql_rows: tuple[_Project, dict[str, set[Row]]], case: Case,
) -> None:
    _check(*codeql_rows, case)


def test_every_parity_row_is_documented() -> None:
    spec = Path(__file__).resolve().parents[1] / "docs/spec-tech/indexing.md"
    text = spec.read_text(encoding="utf-8")
    section = text[text.index("### Engine parity"):text.index("## Build layout")]
    documented = dict(re.findall(r"^\| ([AB]\d+) \|.*?\|.*?\| (.*?) \|$", section, re.MULTILINE))
    rows = {case.id.split("-", 1)[0] for case in CASES}
    assert set(documented) == rows, f"undocumented: {sorted(rows - set(documented))}"
    for case in CASES:
        if case.item:
            assert case.item in documented[case.id.split("-", 1)[0]], (
                f"{case.id}: {case.item} missing from the parity table"
            )
        if case.deviation is not None:
            assert case.id in documented[case.id.split("-", 1)[0]], (
                f"{case.id}: its deviation is missing from the parity table"
            )

