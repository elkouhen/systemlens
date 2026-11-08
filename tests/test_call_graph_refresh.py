"""Regressions of the module refresh of the call-graph view (Copilot Lot 5).

``index --module NAME --refresh-call-graph-view`` runs only with an engine
that ``supports_module_refresh``, such as CodeQL. A recorded engine replays
the ``source`` engine's rows of a two-module fixture, as CodeQL would.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from systemlens.callgraph.engine import EngineRequest
from systemlens.callgraph.model import (
    CallGraphTimeout,
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.callgraph.registry import load_engine
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.sqlite import Store

PACKAGE = "app/src/main/java/example/app"
FILES = {
    "pom.xml": (
        "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
        "<artifactId>parent</artifactId><version>1.0</version><packaging>pom</packaging>"
        "<modules><module>app</module></modules></project>"
    ),
    "app/pom.xml": (
        "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
        "<artifactId>app</artifactId><version>1.0</version></project>"
    ),
    f"{PACKAGE}/OrderController.java": """package example.app;
import org.springframework.web.bind.annotation.*;
@RestController
public class OrderController {
    private final OrderService service;
    public OrderController(OrderService service) { this.service = service; }
    @PostMapping("/orders")
    public void create(@RequestBody String body) {
        service.place(body);
    }
}
""",
    f"{PACKAGE}/OrderService.java": """package example.app;
import org.springframework.stereotype.Service;
@Service
public class OrderService {
    private final Publisher publisher;
    public OrderService(Publisher publisher) { this.publisher = publisher; }
    public void place(String order) {
        publisher.publish(order);
    }
}
""",
    f"{PACKAGE}/Publisher.java": """package example.app;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
@Component
public class Publisher {
    private final KafkaTemplate<String, String> kafkaTemplate;
    public Publisher(KafkaTemplate<String, String> t) { this.kafkaTemplate = t; }
    public void publish(String m) {
        kafkaTemplate.send("orders", m);
    }
}
""",
}


class _RefreshEngine:
    """Replays recorded rows; ``methods`` times out while ``timeout`` is set."""

    name = "recorded"
    label = "Recorded"
    supports_module_refresh = True
    resolves_types = True

    def __init__(self, calls: list[MethodCall], methods: list[SourceMethod]) -> None:
        self._calls = calls
        self._methods = methods
        self.timeout = False

    def available(self) -> bool:
        return True

    def input_fingerprint(self) -> str:
        return ""

    @contextmanager
    def open(self, request: EngineRequest) -> Iterator["_RefreshEngine"]:
        yield self

    def calls(self) -> list[MethodCall]:
        return self._calls

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        if self.timeout:
            raise CallGraphTimeout()
        return [method for method in self._methods if method.path.startswith(source_prefix)]

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        return []

    def payload_types(self) -> list[PayloadTypeEvidence]:
        return []


def _indexed(tmp_path: Path) -> tuple[Path, _RefreshEngine]:
    repo = tmp_path / "repo"
    for relative, text in FILES.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    source = load_engine("source")
    assert source is not None
    java = [path for path in FILES if path.endswith(".java")]
    with source.open(EngineRequest(repo_root=repo, allowed_paths=java, timeout_seconds=60)) as session:
        engine = _RefreshEngine(session.calls(), session.methods(""))
    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True, call_graph_engine=engine)
    return repo, engine


def _view(repo: Path) -> tuple[list[tuple[str, str, int, int]], int, int, list[str]]:
    """Method rows, edge count, dangling edge count and flow IDs."""
    with Store(repo, readonly=True) as store:
        methods = store.all_call_graph_methods()
        edges = store.all_call_edges()
        ids = {method.id for method in methods}
        return (
            sorted((m.id, m.qualified_method, m.start_line, m.end_line) for m in methods),
            len(edges),
            sum(edge.caller_id not in ids or edge.callee_id not in ids for edge in edges),
            sorted(flow.id for flow in store.all_code_flows()),
        )


def _refresh(repo: Path, engine: _RefreshEngine) -> None:
    with Store(repo) as store:
        index_repo(
            repo, Config(), store, call_graph_engine=engine,
            refresh_call_graph_view=True, show_call_chains_module="app",
        )


def test_a_refresh_timeout_keeps_the_persisted_graph(tmp_path: Path) -> None:
    """BC-29: a timed-out refresh joined without the persisted graph.

    Its sink then replaced the view: every method row went, an edge only
    the engine had was lost, and the other edges dangled.
    """
    repo, engine = _indexed(tmp_path)
    before = _view(repo)
    assert before[1] and before[2] == 0 and before[3]

    engine.timeout = True
    _refresh(repo, engine)

    assert _view(repo) == before
    with Store(repo, readonly=True) as store:
        assert store.get_meta("code_flow_snapshot_status") == "partial"
