"""Regressions of the call-graph join and the flow materialisation.

Each test reproduces a finding of the 2026-11-06 review (``BACKLOG.md`` in Git at
``4a12ad5``, section 3.3) through ``index_repo`` with a recorded engine.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from systemlens.callgraph.engine import EngineRequest
from systemlens.callgraph.model import (
    CallGraphTimeout,
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.module_inventory import DiscoveredModule, ModuleDependency
from systemlens.indexing.code_flows import CallGraph, materialize_call_graph_flows
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.persisted_names import META_JOIN_COMPLETED_ENTRIES, META_JOIN_SIGNATURE
from systemlens.storage.sqlite import Store

POM = (
    "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
    "<artifactId>orders</artifactId><version>1.0</version></project>"
)
CONTROLLER = "src/main/java/example/OrderController.java"
GENERATOR = "src/main/java/example/Generator.java"


def _write(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _orders(root: Path) -> Path:
    return _write(root, {
        "pom.xml": POM,
        CONTROLLER: """package example;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/orders")
class OrderController {
  private Generator generator;
  @PostMapping("/generate") public void create() { }
}
""",
        GENERATOR: """package example;
import org.springframework.kafka.core.KafkaTemplate;
class Generator {
  private KafkaTemplate<Long, String> template;
  void generate() { template.send("orders", "new"); }
}
""",
    })


class _RecordedEngine:
    """A typed engine that answers recorded calls; ``timeouts`` first runs time out."""

    name = "recorded"
    label = "Recorded"
    supports_module_refresh = False
    resolves_types = True

    def __init__(
        self, calls: list[MethodCall], timeouts: int = 0, methods: list[SourceMethod] | None = None,
    ) -> None:
        self._calls = calls
        self._timeouts = timeouts
        self._methods = methods or []
        self.runs = 0

    def available(self) -> bool:
        return True

    def input_fingerprint(self) -> str:
        return ""

    @contextmanager
    def open(self, request: EngineRequest) -> Iterator["_RecordedEngine"]:
        yield self

    def calls(self) -> list[MethodCall]:
        self.runs += 1
        if self.runs <= self._timeouts:
            raise CallGraphTimeout(calls=[])
        return self._calls

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        return self._methods

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        return []

    def payload_types(self) -> list[PayloadTypeEvidence]:
        return []


def _kinds(store: Store) -> list[list[str]]:
    return [[step.kind for step in flow.steps] for flow in store.all_code_flows()]


def test_an_engine_timeout_leaves_no_resumable_join_checkpoint(tmp_path: Path) -> None:
    """Review A1: a resume after a timeout persisted partial flows as complete.

    The timeout path kept the join signature and set the join offset to
    the total, so ``--resume-call-graph-join`` joined nothing and marked
    the empty flow set ``complete``. The resume must refuse, and a normal
    index must retry the engine.
    """
    repo = _orders(tmp_path / "repo")
    engine = _RecordedEngine([MethodCall(
        "example.OrderController.create", CONTROLLER, 7,
        "example.Generator.generate", GENERATOR, 5, 7,
    )], timeouts=1)

    with Store(repo) as store:
        first = index_repo(repo, Config(), store, full=True, call_graph_engine=engine)
        assert first.call_graph_timed_out is True
        assert store.get_meta("code_flow_snapshot_status") == "partial"
        assert store.get_meta(META_JOIN_SIGNATURE) is None
        assert store.get_meta(META_JOIN_COMPLETED_ENTRIES) is None

    with Store(repo) as store, pytest.raises(ValueError, match="timed out"):
        index_repo(repo, Config(), store, call_graph_engine=engine, resume_call_graph_join=True)

    with Store(repo) as store:
        second = index_repo(repo, Config(), store, call_graph_engine=engine)
        assert second.call_graph_timed_out is False
        assert store.get_meta("code_flow_snapshot_status") == "complete"
        assert _kinds(store) == [["http_entry", "method_call", "message_publish"]]


SVC = "src/main/java/example/Svc.java"
SVC_IMPL = "src/main/java/example/SvcImpl.java"
HELPER = "src/main/java/example/Helper.java"


def _interface_chain(root: Path) -> Path:
    return _write(root, {
        "pom.xml": POM,
        CONTROLLER: """package example;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/orders")
class OrderController {
  private Svc svc;
  @PostMapping("/generate") public void create() { svc.place(); }
}
""",
        SVC: "package example;\ninterface Svc {\n  void place();\n}\n",
        SVC_IMPL: """package example;
import org.springframework.kafka.core.KafkaTemplate;
class SvcImpl implements Svc {
  private KafkaTemplate<Long, String> template;
  private Helper helper;
  public void place() { helper.a(); }
}
""",
        HELPER: """package example;
import org.springframework.kafka.core.KafkaTemplate;
class Helper {
  private KafkaTemplate<Long, String> template;
  void a() { b(); }
  void b() { c(); }
  void c() { template.send("orders", "new"); }
}
""",
    })


def test_a_witness_adds_no_second_edge_to_the_exact_target(tmp_path: Path) -> None:
    """Review A4: the witness doubled the exact edge as a possible, inferred one.

    The search keyed its visited set by ``(target, possible)``, so every
    method below the interface call was explored twice: 9 transitions
    instead of 5 on this four-hop chain. ADR-70's witness hop stays.
    """
    repo = _interface_chain(tmp_path / "repo")
    engine = _RecordedEngine([
        MethodCall("example.OrderController.create", CONTROLLER, 7,
                   "example.SvcImpl.place", SVC_IMPL, 6, 7, "exact"),
        MethodCall("example.OrderController.create", CONTROLLER, 7,
                   "example.Svc.place", SVC, 3, 7, "possible"),
        MethodCall("example.SvcImpl.place", SVC_IMPL, 6, "example.Helper.a", HELPER, 5, 6, "exact"),
        MethodCall("example.Helper.a", HELPER, 5, "example.Helper.b", HELPER, 6, 5, "exact"),
        MethodCall("example.Helper.b", HELPER, 6, "example.Helper.c", HELPER, 7, 6, "exact"),
    ])
    progress: list[str] = []

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True, call_graph_engine=engine,
                   progress=progress.append)
        names = {method.id: method.qualified_method for method in store.all_call_graph_methods()}
        for method in store.all_integration_methods():
            names.setdefault(method.id, method.qualified_method)
        create = sorted(
            (names[edge.callee_id], edge.dispatch_confidence, edge.inferred)
            for edge in store.all_call_edges()
            if names.get(edge.caller_id) == "example.OrderController.create"
        )
        flows = store.all_code_flows()

    assert create == [
        ("example.Svc.place", "possible", False),
        ("example.SvcImpl.place", "exact", False),
    ]
    summary = next(message for message in reversed(progress) if "transition(s)" in message)
    assert " 5 transition(s)" in summary, summary
    assert [(flow.confidence, flow.alternative_count) for flow in flows] == [("medium", 1)]


def _graph_edges(
    root: Path, files: dict[str, str], modules: list[str], calls: list[MethodCall],
    dependencies: list[ModuleDependency],
) -> set[tuple[str, str, int, str, bool]]:
    _write(root, files)
    discovered = [
        DiscoveredModule(name, root / name, "maven", None, "library", False, "")
        for name in modules
    ]
    methods = materialize_integration_methods(root, [], list(files), discovered)
    names = {method.id: method.qualified_method for method in methods}
    graphs: list[CallGraph] = []
    materialize_call_graph_flows(
        methods, [], calls, repo_root=root, modules=discovered,
        module_dependencies=dependencies, call_graph_sink=graphs.append,
    )
    return {
        (names.get(edge.caller_id, edge.caller_id), names.get(edge.callee_id, edge.callee_id),
         edge.line, edge.dispatch_confidence, edge.inferred)
        for edge in graphs[-1].edges()
    }


SHAPE = "a/src/main/java/a/Shape.java"
SQUARE = "a/src/main/java/a/Square.java"
CIRCLE = "b/src/main/java/b/Circle.java"
USE_C = "c/src/main/java/c/UseC.java"


def test_a_typed_engine_row_stays_inside_the_callers_visible_modules(tmp_path: Path) -> None:
    """Review J2 (C6 of the dispatch-scope plan): ADR-71 for typed engines.

    Joern dispatched ``Shape.area`` in module ``c`` to ``b.Circle.area``,
    although ``c`` and ``b`` are unrelated by the build graph. Neither the
    engine row, the join's dispatch expansion nor the symbol fallback may
    reach ``b``.
    """
    files = {
        SHAPE: "package a;\npublic interface Shape { double area(); }\n",
        SQUARE: "package a;\npublic class Square implements Shape { public double area() { return 1.0; } }\n",
        CIRCLE: "package b;\nimport a.Shape;\npublic class Circle implements Shape { public double area() { return 3.14; } }\n",
        USE_C: "package c;\nimport a.Shape;\nimport a.Square;\n"
               "public class UseC { public double go() { Shape s = new Square(); return s.area(); } }\n",
    }
    go = ("c.UseC.go", USE_C, 4)
    calls = [
        MethodCall(*go, "a.Shape.area", SHAPE, 2, 4, "possible"),
        MethodCall(*go, "a.Square.area", SQUARE, 2, 4, "possible"),
        MethodCall(*go, "b.Circle.area", CIRCLE, 3, 4, "possible"),
    ]

    edges = _graph_edges(
        tmp_path, files, ["a", "b", "c"], calls,
        [ModuleDependency("b", "a"), ModuleDependency("c", "a")],
    )

    assert {(caller, callee) for caller, callee, *_ in edges} == {
        ("c.UseC.go", "a.Shape.area"), ("c.UseC.go", "a.Square.area"),
    }
    # With ``c`` depending on ``b``, the row stands; so it does when ``b``
    # depends on ``c``: a module built on the caller's can implement its
    # interfaces, as an adapter implements a port.
    for dependency in (ModuleDependency("c", "b"), ModuleDependency("b", "c")):
        visible = _graph_edges(
            tmp_path, files, ["a", "b", "c"], calls,
            [ModuleDependency("b", "a"), ModuleDependency("c", "a"), dependency],
        )
        assert ("c.UseC.go", "b.Circle.area", 4, "possible", False) in visible


def test_an_enum_constant_body_keeps_a_typed_engine_call_possible(tmp_path: Path) -> None:
    """Review J4: Joern has no method for ``BIG``'s body.

    ``Square.Kind.BIG.w()`` was ``exact`` to ``Kind.w`` while ``BIG``
    declares its own ``w``. Both bodies are ``possible`` targets (H9).
    """
    square = "a/src/main/java/a/Square.java"
    use = "a/src/main/java/a/Use.java"
    files = {
        square: """package a;
public class Square {
    public enum Kind {
        BIG { public int w() { return 9; } },
        SMALL;
        public int w() { return 1; }
    }
}
""",
        use: "package a;\nclass Use { int go() { return Square.Kind.BIG.w(); } }\n",
    }
    calls = [MethodCall("a.Use.go", use, 2, "a.Square.Kind.w", square, 6, 2, "exact")]

    edges = _graph_edges(tmp_path, files, ["a"], calls, [])

    assert {(callee, line, confidence) for _caller, callee, line, confidence, _ in edges} == {
        ("a.Square.Kind.w", 2, "possible"),
    }
    assert len(edges) == 2
    assert all(confidence == "possible" for *_, confidence, _inferred in edges)


def test_an_anonymous_class_method_of_an_engine_is_not_persisted(tmp_path: Path) -> None:
    """Review A5: ``<anonymous class>.run`` became a placeholder method row.

    ADR-62 gives an anonymous class method no identity; the join already
    attributes its calls to the creating method.
    """
    repo = _orders(tmp_path / "repo")
    engine = _RecordedEngine([], methods=[
        SourceMethod("<anonymous class>.run", CONTROLLER, 7),
        SourceMethod("example.Generator.generate", GENERATOR, 5),
    ])

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True, call_graph_engine=engine)
        names = [method.qualified_method for method in store.all_call_graph_methods()]

    assert "example.Generator.generate" in names
    assert not any(name.startswith("<anonymous") for name in names)


def test_an_engine_constructor_joins_its_node_and_a_default_one_is_no_placeholder(
    tmp_path: Path,
) -> None:
    """Review J5, ADR-75: engines report constructors as ``Owner.<init>``.

    A typed engine locates a constructor on its name line and an implicit
    one on the class line; both join the index's nodes. A default
    constructor that runs no code is no node of the index, and the join
    adds no placeholder for it.
    """
    repo = _write(tmp_path / "repo", {
        "pom.xml": POM,
        CONTROLLER: """package example;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/orders")
class OrderController {
  @PostMapping("/generate") public void create() { new Job(); }
}
""",
        "src/main/java/example/Job.java": """package example;
class Job {
  @Deprecated
  Job() { new Plain(); }
}
""",
        "src/main/java/example/Plain.java": "package example;\nclass Plain {\n}\n",
    })
    job = "src/main/java/example/Job.java"
    engine = _RecordedEngine([
        MethodCall("example.OrderController.create", CONTROLLER, 6,
                   "example.Job.<init>", job, 4, 6, "exact", (), ()),
        MethodCall("example.Job.<init>", job, 4,
                   "example.Plain.<init>", "src/main/java/example/Plain.java", 2, 4, "exact", (), ()),
    ])

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True, call_graph_engine=engine)
        names = {method.id: method.qualified_method for method in store.all_call_graph_methods()}
        for method in store.all_integration_methods():
            names.setdefault(method.id, method.qualified_method)
        edges = {(names[edge.caller_id], names[edge.callee_id]) for edge in store.all_call_edges()}

    assert edges == {("example.OrderController.create", "example.Job.<init>")}
    assert "example.Plain.<init>" not in names.values()
