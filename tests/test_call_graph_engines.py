import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from systemlens.application.engine_comparison import (
    compare_engines,
    render_engine_comparison_text,
)
from systemlens.callgraph.engine import EngineRequest, EngineSettings
from systemlens.callgraph.model import (
    CallGraphTimeout,
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.callgraph.registry import declarations, engine_names, load_engine
from systemlens.engines.custom.engine import SourceEngine
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.engines import codeql
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config, ConfigError, init_config, load_config
from systemlens.storage.sqlite import Store


def test_the_core_never_imports_an_engine_adapter() -> None:
    """The core reads engine declarations only; adapters load when built."""
    script = (
        "import sys\n"
        "import systemlens.indexing.service, systemlens.indexing.code_flows\n"
        "import systemlens.application.flow_calculation, systemlens.application.engine_comparison\n"
        "import systemlens.delivery.cli, systemlens.render\n"
        "from systemlens.callgraph.registry import declarations\n"
        "declarations()\n"
        "loaded = sorted(name for name in sys.modules if name.startswith('systemlens.engines.')\n"
        "                and not name.endswith('.declaration')\n"
        "                and name.count('.') > 2)\n"
        "assert not loaded, f'core imported an engine adapter: {loaded}'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_registry_builds_declared_engines_from_their_settings() -> None:
    assert load_engine("none") is None
    assert isinstance(load_engine("source"), SourceEngine)
    engine = load_engine("codeql", EngineSettings(
        threads=2, ram_mb=512, options={"database": "/db", "verbosity": "progress"},
    ))
    assert isinstance(engine, codeql.CodeQLEngine)
    assert (engine.database, engine.threads, engine.ram_mb, engine.verbosity) == (
        Path("/db"), 2, 512, "progress",
    )
    assert engine.available()
    with pytest.raises(ValueError, match="inconnu"):
        load_engine("javaparser")


def test_each_declaration_matches_the_engine_it_builds() -> None:
    names = engine_names()
    assert names[-1] == "none" and "source" in names
    for item in declarations():
        engine = load_engine(item.name)
        assert engine is not None
        assert (engine.name, engine.label) == (item.name, item.label)
        assert engine.supports_module_refresh == item.supports_module_refresh
        assert engine.resolves_types == item.resolves_types
        if item.database_option is not None:
            assert item.option(item.database_option).kind == "path"


def test_configuration_defaults_to_the_source_engine(tmp_path: Path) -> None:
    init_config(tmp_path)
    config_file = tmp_path / ".systemlens" / "config.yml"
    text = config_file.read_text(encoding="utf-8")
    assert "call_graph_engine: source" in text
    assert load_config(tmp_path).call_graph_engine == "source"
    assert Config().call_graph_engine == "source"

    # A configuration without any engine setting uses the source engine.
    config_file.write_text("analysis: {}\n", encoding="utf-8")
    assert load_config(tmp_path).call_graph_engine == "source"

    config_file.write_text(text.replace("call_graph_engine: source", "call_graph_engine: codeql"), encoding="utf-8")
    assert load_config(tmp_path).call_graph_engine == "codeql"

    # The removed ``codeql`` switch no longer selects an engine: it is an
    # unknown key (ADR-85).
    config_file.write_text("analysis:\n  codeql: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="inconnue"):
        load_config(tmp_path)

    config_file.write_text(text.replace("call_graph_engine: source", "call_graph_engine: javaparser"), encoding="utf-8")
    with pytest.raises(ConfigError, match="call_graph_engine"):
        load_config(tmp_path)


def _repository(root: Path) -> Path:
    source = root / "src/main/java/example"
    source.mkdir(parents=True)
    (root / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
        "<artifactId>orders</artifactId><version>1.0</version></project>",
        encoding="utf-8",
    )
    (source / "OrderController.java").write_text(
        """package example;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/orders")
class OrderController {
  private Generator generator;
  @PostMapping("/generate") public void create() { generator.generate(); }
}
""",
        encoding="utf-8",
    )
    (source / "Generator.java").write_text(
        """package example;
import org.springframework.kafka.core.KafkaTemplate;
class Generator {
  private KafkaTemplate<Long, String> template;
  void generate() { template.send("orders", "new"); }
}
""",
        encoding="utf-8",
    )
    return root


def test_an_unavailable_engine_falls_back_to_the_source_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path / "repo")
    monkeypatch.setattr(codeql.database, "codeql_executable", lambda: None)
    messages: list[str] = []

    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="codeql"), store, full=True, progress=messages.append)
        flows = store.all_code_flows()
        edges = store.all_call_edges()

    assert any("CodeQL indisponible ; moteur source (tree-sitter) utilisé" in item for item in messages)
    assert [step.kind for step in flows[0].steps] == ["http_entry", "method_call", "message_publish"]
    assert edges, "the source engine persists its call graph like any engine"


class _RecordedEngine:
    """An engine answering with fixed calls, like a typed analyzer would."""

    name = "recorded"
    label = "Recorded"
    supports_module_refresh = False
    resolves_types = True

    def __init__(self, calls: list[MethodCall]) -> None:
        self._calls = calls
        self.requests: list[EngineRequest] = []

    def available(self) -> bool:
        return True

    @contextmanager
    def open(self, request: EngineRequest) -> Iterator["_RecordedEngine"]:
        self.requests.append(request)
        yield self

    def calls(self) -> list[MethodCall]:
        return self._calls

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        return []

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        return []

    def payload_types(self) -> list[PayloadTypeEvidence]:
        return []


def test_an_injected_engine_drives_flow_construction(tmp_path: Path) -> None:
    repo = _repository(tmp_path / "repo")
    engine = _RecordedEngine([MethodCall(
        "example.OrderController.create", "src/main/java/example/OrderController.java", 7,
        "example.Generator.generate", "src/main/java/example/Generator.java", 5, 7,
    )])

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True, call_graph_engine=engine)
        flows = store.all_code_flows()

    [request] = engine.requests
    assert request.repo_root == repo
    assert "src/main/java/example/Generator.java" in request.allowed_paths
    assert [step.name for step in flows[0].steps][1:] == ["example.Generator.generate", "orders"]
    assert flows[0].confidence == "medium"
    assert "The call graph resolved a static call path" in flows[0].reason


def test_engine_comparison_reports_shared_and_missing_flows(tmp_path: Path) -> None:
    repo = _repository(tmp_path / "repo")
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="source"), store, full=True)

    with Store(repo, readonly=True) as store:
        result = compare_engines(store, [SourceEngine(), _RecordedEngine([MethodCall(
            "example.OrderController.create", "src/main/java/example/OrderController.java", 7,
            "example.Generator.generate", "src/main/java/example/Generator.java", 5, 7,
        )])])
        with pytest.raises(ValueError, match="distincts"):
            compare_engines(store, [SourceEngine(), SourceEngine()])

    assert result["engines"] == ["source", "recorded"]
    flows = result["flows"]
    assert isinstance(flows, dict)
    assert flows["both"] == 1
    assert flows["only_source_count"] == 0 and flows["only_recorded_count"] == 0
    runs = result["runs"]
    assert isinstance(runs, dict)
    assert runs["source"]["flows"] == 1 and runs["recorded"]["calls"] == 1
    text = render_engine_comparison_text(result)
    assert "Flux (paires entrée, déclencheur, sortie) : 1 communs, 0 seulement source, 0 seulement recorded," in text
    with Store(repo, readonly=True) as store:
        assert len(store.all_code_flows()) == 1, "comparison never persists flows"


class _NoReachabilityEngine(_RecordedEngine):
    name = "recorded"

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        raise AssertionError("the comparison runs no reachability query (ADR-84)")


def test_engine_comparison_keys_flows_by_entry_trigger_and_output(tmp_path: Path) -> None:
    """BL-53: two routes between the same ends are one shared flow.

    The source engine reaches the publisher directly; the recorded engine
    through ``Relay.forward``. The pair is common, its routes are listed
    apart, and no reachability query runs.
    """
    repo = _repository(tmp_path / "repo")
    (repo / "src/main/java/example/Relay.java").write_text(
        """package example;
class Relay {
  private Generator generator;
  void forward() { generator.generate(); }
}
""",
        encoding="utf-8",
    )
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="source"), store, full=True)
    recorded = _NoReachabilityEngine([
        MethodCall(
            "example.OrderController.create", "src/main/java/example/OrderController.java", 7,
            "example.Relay.forward", "src/main/java/example/Relay.java", 4, 7,
        ),
        MethodCall(
            "example.Relay.forward", "src/main/java/example/Relay.java", 4,
            "example.Generator.generate", "src/main/java/example/Generator.java", 5, 4,
        ),
    ])
    with Store(repo, readonly=True) as store:
        result = compare_engines(store, [SourceEngine(), recorded])

    flows = result["flows"]
    assert isinstance(flows, dict)
    assert flows["both"] == 1
    assert flows["only_source_count"] == 0 and flows["only_recorded_count"] == 0
    routes = result["routes"]
    assert isinstance(routes, dict)
    assert routes["pairs_with_different_routes_count"] == 1
    [difference] = routes["pairs_with_different_routes"]
    assert difference["only_source"] == ["example.Generator.generate"]
    assert difference["only_recorded"] == ["example.Relay.forward → example.Generator.generate"]
    runs = result["runs"]
    assert isinstance(runs, dict)
    assert runs["recorded"]["flow_pairs"] == 1
    text = render_engine_comparison_text(result)
    assert "Routes : 1 paire(s) commune(s) reliée(s) par des routes différentes." in text
    assert "    [recorded] example.Relay.forward → example.Generator.generate" in text


def test_engine_comparison_joins_the_persisted_data_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review D1: the comparison rebuilds flows from the indexing inputs.

    Without the persisted data operations the module projection turns
    unknown derived queries into writes, and the totals diverge from the
    index (352 flows instead of 406 on train-ticket).
    """
    from systemlens.application import engine_comparison

    repo = _repository(tmp_path / "repo")
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="source"), store, full=True)
    received: list[object] = []
    original = engine_comparison.materialize_call_graph_flows

    def recording(*args: object, **kwargs: object) -> object:
        received.append(kwargs.get("operations"))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_comparison, "materialize_call_graph_flows", recording)
    with Store(repo, readonly=True) as store:
        compare_engines(store, [SourceEngine(), _RecordedEngine([])])
        persisted = store.all_data_operations()

    assert received == [persisted, persisted], "each engine joins the persisted operations"


def test_engine_comparison_compares_edges_inside_the_shared_method_universe(
    tmp_path: Path,
) -> None:
    """Review D3: edges are keyed by site and compared on their labels.

    An edge to a method only one engine models is reported apart, and an
    edge both engines find with another confidence is a confidence change.
    """
    repo = _repository(tmp_path / "repo")
    generator = repo / "src/main/java/example/Generator.java"
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="source"), store, full=True)
        indexed = {method.qualified_method for method in store.all_integration_methods()}
    assert "example.Generator.generate" in indexed

    recorded = _RecordedEngine([
        MethodCall(
            "example.OrderController.create", "src/main/java/example/OrderController.java", 7,
            "example.Generator.generate", "src/main/java/example/Generator.java", 5, 7,
            "possible",
        ),
        # A library-like helper only the recorded engine models.
        MethodCall(
            "example.Generator.generate", "src/main/java/example/Generator.java", 5,
            "example.Generator.helper", "src/main/java/example/Generator.java", 4, 5,
        ),
    ])
    assert generator.is_file()
    with Store(repo, readonly=True) as store:
        result = compare_engines(store, [SourceEngine(), recorded])

    edges = result["edges"]
    assert isinstance(edges, dict)
    assert edges["only_recorded_count"] == 0, "a helper edge is not a difference"
    assert edges["confidence_changes_count"] == 1
    assert edges["confidence_changes"] == [
        "example.OrderController.create:7 → example.Generator.generate:5 "
        "(src/main/java/example/OrderController.java:7) : exact → possible"
    ]
    outside = result["edges_outside_shared_methods"]
    assert isinstance(outside, dict)
    assert outside["source"]["count"] == 0
    assert outside["recorded"]["count"] == 1
    runs = result["runs"]
    assert isinstance(runs, dict)
    assert runs["recorded"]["edges_outside_shared_methods"] == 1
    assert "1 changement(s) de confiance" in render_engine_comparison_text(result)


def test_engine_comparison_keys_flows_by_their_full_route() -> None:
    """Review D3: two flows with the same ends and another route stay two."""
    from systemlens.application.engine_comparison import _flow_key
    from systemlens.domain.code_flows import CodeFlow, CodeFlowStep

    def flow(middle: str, line: int) -> CodeFlow:
        return CodeFlow(
            "id-" + middle, "orders", "example.OrderController.create",
            "src/main/java/example/OrderController.java", 7, 7, "potential", "medium", "",
            (
                CodeFlowStep(1, "http_entry", "POST /orders", "OrderController.java", 7, 7, "in"),
                CodeFlowStep(2, "method_call", middle, "OrderController.java", line, line),
                CodeFlowStep(3, "data_read", "orders", "Repository.java", line, line),
            ),
        )

    assert _flow_key(flow("a.B.first", 8)) != _flow_key(flow("a.B.second", 9))


def _derived_query_repository(root: Path) -> list[str]:
    files = {
        "app/src/main/java/p/OrderRepository.java": (
            "package p;\n"
            "import org.springframework.data.mongodb.repository.MongoRepository;\n"
            "interface OrderRepository extends MongoRepository<Order, String> {\n"
            "  Order findByOrderId(String orderId);\n"
            "}\n"
        ),
        "app/src/main/java/p/Order.java": "package p;\nclass Order {}\n",
        "app/src/main/java/p/OrderService.java": (
            "package p;\n"
            "class OrderService {\n"
            "  private OrderRepository repository;\n"
            "  Order find(String id) {\n"
            "    return repository.findByOrderId(id);\n"
            "  }\n"
            "}\n"
        ),
    }
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    return list(files)


def _derived_query_edges(
    root: Path, paths: list[str], calls: list[MethodCall], *, closed_world: bool,
) -> list[tuple[str, str, int, str, bool]]:
    from systemlens.domain.module_inventory import DiscoveredModule
    from systemlens.indexing.code_flows import _build_call_graph
    from systemlens.indexing.integration_methods import materialize_integration_methods

    module = DiscoveredModule(
        name="app", path=root / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(root, [], paths, [module])
    graph = _build_call_graph(
        methods, calls, repo_root=root, source_paths=paths, progress=None,
        closed_world=closed_world, modules=[module],
    )
    names = {method.id: method.qualified_method for method in methods}
    return [
        (names[edge.caller_id], names[edge.callee_id], edge.line,
         edge.dispatch_confidence, edge.inferred)
        for edge in graph.edges()
    ]


def test_a_derived_query_call_is_a_declared_possible_edge_in_both_engines(
    tmp_path: Path,
) -> None:
    """Review D4: a call the receiver type declares is not an inference.

    A Spring Data query method has no source body. The source engine used
    to label its call ``inferred``, and the CodeQL join added that inferred
    edge next to CodeQL's own row: 192 duplicated sites on train-ticket.
    """
    paths = _derived_query_repository(tmp_path)
    expected = [
        ("p.OrderService.find", "p.OrderRepository.findByOrderId", 5, "possible", False),
    ]

    assert _derived_query_edges(tmp_path, paths, [], closed_world=True) == expected
    codeql_row = MethodCall(
        "p.OrderService.find", "app/src/main/java/p/OrderService.java", 4,
        "p.OrderRepository.findByOrderId", "app/src/main/java/p/OrderRepository.java", 4, 5,
        "possible",
    )
    assert _derived_query_edges(tmp_path, paths, [codeql_row], closed_world=False) == expected


def test_a_source_engine_flow_through_static_calls_says_so(tmp_path: Path) -> None:
    """Review F14: the reason names the route, not the engine that built it.

    Every source-engine call is a symbol-pass call; an exact one is still a
    static call, as the same route built from engine rows says.
    """
    repo = _repository(tmp_path / "repo")
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="source"), store, full=True)
        [flow] = store.all_code_flows()

    assert flow.confidence == "medium"
    assert "The call graph resolved a static call path" in flow.reason


class _MethodQueryTimeoutEngine(_RecordedEngine):
    """Returns its calls, then reaches the deadline in the methods query."""

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        raise CallGraphTimeout(calls=[])


def test_a_deadline_after_the_call_query_keeps_its_calls(tmp_path: Path) -> None:
    """Q3 of the call-graph review: a timeout in the methods, payload or
    reachability query must not discard the calls already returned."""
    repo = _repository(tmp_path / "repo")
    controller = repo / "src/main/java/example/OrderController.java"
    # Only the engine knows this call: the symbol pass sees an empty body.
    controller.write_text(
        controller.read_text(encoding="utf-8").replace("{ generator.generate(); }", "{ }"),
        encoding="utf-8",
    )
    engine = _MethodQueryTimeoutEngine([MethodCall(
        "example.OrderController.create", "src/main/java/example/OrderController.java", 7,
        "example.Generator.generate", "src/main/java/example/Generator.java", 5, 7,
    )])

    with Store(repo) as store:
        report = index_repo(repo, Config(), store, full=True, call_graph_engine=engine)
        flows = store.all_code_flows()
        status = store.get_meta("code_flow_snapshot_status")

    assert report.call_graph_timed_out is True
    assert status == "partial"
    assert [[step.kind for step in flow.steps] for flow in flows] == [
        ["http_entry", "method_call", "message_publish"],
    ]


class _NoReachabilityEngine(_RecordedEngine):
    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        raise AssertionError("the index does not run the reachability query")


def test_the_index_runs_no_reachability_query(tmp_path: Path) -> None:
    """Q12 of the call-graph review: since ADR-78 a reachability row adds
    no flow the join does not find, so the index no longer pays for it."""
    repo = _repository(tmp_path / "repo")
    engine = _NoReachabilityEngine([MethodCall(
        "example.OrderController.create", "src/main/java/example/OrderController.java", 7,
        "example.Generator.generate", "src/main/java/example/Generator.java", 5, 7,
    )])

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True, call_graph_engine=engine)
        assert len(store.all_code_flows()) == 1


class _MethodsEngine(_RecordedEngine):
    """Reports engine methods the way CodeQL spells and locates them."""

    def __init__(self, methods: list[SourceMethod]) -> None:
        super().__init__([])
        self._methods = methods

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        return self._methods


def test_engine_methods_never_duplicate_an_indexed_method(tmp_path: Path) -> None:
    """Q10 of the call-graph review: CodeQL's raw ``$`` name and declaration
    line stored each annotated or nested method a second time."""
    repo = _repository(tmp_path / "repo")
    path = "src/main/java/example/Outer.java"
    (repo / path).write_text(
        "package example;\n"
        "class Outer {\n"
        "  @Deprecated\n"
        "  void annotated() { }\n"
        "  static class Inner {\n"
        "    void nested() { }\n"
        "  }\n"
        "  void plain() { }\n"
        "}\n",
        encoding="utf-8",
    )
    engine = _MethodsEngine([
        SourceMethod("example.Outer.annotated", path, 4),
        SourceMethod("example.Outer$Inner.nested", path, 6),
        SourceMethod("example.Outer$Inner.<init>", path, 5),
    ])

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True, call_graph_engine=engine)
        rows = [
            (method.qualified_method, method.start_line)
            for method in store.all_call_graph_methods() if method.path == path
        ]

    assert sorted(rows) == [
        ("example.Outer.Inner.<init>", 5),
        ("example.Outer.Inner.nested", 6),
        ("example.Outer.annotated", 3),
        ("example.Outer.plain", 8),
    ]


def test_source_calls_reach_same_line_overloads(tmp_path: Path) -> None:
    """BC-23: overloads declared on one line share a name, a file and a line.

    The join tells them apart by the parameter types a call carries. The
    symbol pass left them out, so ``locate`` found two candidates and
    dropped every edge to or from such an overload.
    """
    path = "app/src/main/java/p/Over.java"
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(
        "package p;\n"
        "class Over { void f(int x) {} void f(String s) {} }\n"
        "class Use {\n"
        "  void run(Over o) {\n"
        "    o.f(1);\n"
        "    o.f(\"a\");\n"
        "  }\n"
        "}\n"
        "class Callers { void t() {} void g(int x) { t(); } void g(String s) { t(); } }\n",
        encoding="utf-8",
    )
    edges = _derived_query_edges(tmp_path, [path], [], closed_world=True)
    assert sorted((caller, callee, line) for caller, callee, line, _confidence, _inferred in edges) == [
        ("p.Callers.g", "p.Callers.t", 9),
        ("p.Callers.g", "p.Callers.t", 9),
        ("p.Use.run", "p.Over.f", 5),
        ("p.Use.run", "p.Over.f", 6),
    ]
