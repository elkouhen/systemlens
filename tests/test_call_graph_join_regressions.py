"""Regressions of the call-graph join and the flow materialisation.

Each test reproduces a finding of the 2026-11-06 review (``BACKLOG.md``,
section 3.3) through ``index_repo`` with a recorded engine.
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

    def __init__(self, calls: list[MethodCall], timeouts: int = 0) -> None:
        self._calls = calls
        self._timeouts = timeouts
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
        return []

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
