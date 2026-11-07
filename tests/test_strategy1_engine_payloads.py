"""Each engine's share of Strategy1: Kafka payload evidence (ADR-85, ADR-5).

``tests/fixtures/strategy1_payloads`` sends two Strategy1 payloads. The
scanner types ``created`` from its parameter (line 13), but not ``event``,
a local declared ``Object`` and assigned an ``OrderCreated`` (line 9).
Only an engine's data flow completes it, and only under ``--strategy
strategy1``: the default strategy never asks the engine.
"""

import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from systemlens.callgraph.engine import CallGraphEngine, EngineRequest
from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.callgraph.registry import declaration, load_engine
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.engines.joern.extraction import parse_payload_types
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.sqlite import Store

FIXTURE = Path(__file__).parent / "fixtures" / "strategy1_payloads"
PUBLISHER = "src/main/java/com/example/Publisher.java"


class _CountingEngine:
    """Delegates to a real engine and counts the ``payload_types`` calls."""

    def __init__(self, engine: CallGraphEngine) -> None:
        self._engine = engine
        self.name = engine.name
        self.label = engine.label
        self.supports_module_refresh = engine.supports_module_refresh
        self.resolves_types = engine.resolves_types
        self.payload_requests = 0

    def available(self) -> bool:
        return self._engine.available()

    def input_fingerprint(self) -> str:
        return self._engine.input_fingerprint()

    @contextmanager
    def open(self, request: EngineRequest) -> Iterator["_CountingSession"]:
        with self._engine.open(request) as session:
            yield _CountingSession(session, self)


class _CountingSession:
    def __init__(self, session: object, engine: _CountingEngine) -> None:
        self._session = session
        self._engine = engine

    def calls(self) -> list[MethodCall]:
        return self._session.calls()  # type: ignore[attr-defined, no-any-return]

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        return self._session.methods(source_prefix)  # type: ignore[attr-defined, no-any-return]

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        return self._session.reachability(methods)  # type: ignore[attr-defined, no-any-return]

    def payload_types(self) -> list[PayloadTypeEvidence]:
        self._engine.payload_requests += 1
        return self._session.payload_types()  # type: ignore[attr-defined, no-any-return]


def _index(tmp_path: Path, engine_name: str, strategy: str) -> tuple[dict[int, str | None], int]:
    repo = tmp_path / strategy / "repo"
    shutil.copytree(FIXTURE, repo)
    engine = load_engine(engine_name)
    assert engine is not None
    counting = _CountingEngine(engine)
    with Store(repo) as store:
        index_repo(repo, Config(strategy=strategy), store, full=True, call_graph_engine=counting)
        types = {
            endpoint.start_line: endpoint.message_type
            for endpoint in store.all_endpoints()
            if endpoint.framework == "kafka-topic-strategy1" and endpoint.path == PUBLISHER
        }
    return types, counting.payload_requests


def _available(engine: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        declaration(engine).unavailable_reason() is not None,
        reason=f"{engine} is not installed",
    )


@pytest.mark.parametrize(("engine", "completed"), [
    # The in-house engine has no data flow beyond the scanner's AST rule.
    ("source", None),
    pytest.param("codeql", "OrderCreated", marks=[pytest.mark.slow, _available("codeql")]),
    pytest.param("joern", "OrderCreated", marks=[pytest.mark.slow, _available("joern")]),
])
def test_an_engine_completes_a_strategy1_payload_only_under_strategy1(
    tmp_path: Path, engine: str, completed: str | None,
) -> None:
    strategy1, asked = _index(tmp_path, engine, "strategy1")
    assert asked == 1
    assert strategy1 == {9: completed, 13: "OrderCreated"}

    default, asked = _index(tmp_path, engine, "default")
    assert asked == 0
    assert default == {}


def _send(**fields: object) -> dict[str, object]:
    return {"file": PUBLISHER, "line": 9, "assignments": [], **fields}


def test_joern_payload_evidence_follows_the_codeql_rules() -> None:
    """Wrappers by written type argument, locals by assignment, else the type."""
    sends = [
        _send(line=1, type="java.lang.Object", variable="event", declaration="Object event",
              assignments=[{"target": "event", "type": "com.example.OrderCreated", "variable": ""}]),
        _send(line=2, type="java.lang.Object", variable="second", declaration="Object second",
              assignments=[
                  {"target": "first", "type": "com.example.OrderCreated", "variable": ""},
                  {"target": "second", "type": "java.lang.Object", "variable": "first"},
              ]),
        _send(line=3, type="org.springframework.messaging.Message", variable="message",
              declaration="final Message<com.example.OrderCreated> message"),
        _send(line=4, type="org.apache.kafka.clients.producer.ProducerRecord", variable="record",
              declaration="ProducerRecord<String, OrderCreated> record"),
        _send(line=5, type="org.springframework.messaging.Message", variable="raw",
              declaration="Message<?> raw"),
        _send(line=6, type="java.util.List", variable="events", declaration="List<OrderCreated> events"),
        _send(line=7, type="com.example.OrderCreated", variable="", declaration=""),
        _send(line=8, type="ANY", variable="", declaration=""),
        _send(file="src/main/java/com/example/Other.java", line=9,
              type="com.example.OrderCreated", variable="", declaration=""),
    ]
    document = {"schema": "systemlens-joern-v1", "methods": [], "calls": [], "payload_sends": sends}

    evidence = parse_payload_types(document, [PUBLISHER])

    assert {(item.line, item.message_type) for item in evidence} == {
        (1, "OrderCreated"), (2, "OrderCreated"), (3, "OrderCreated"), (4, "OrderCreated"),
        (6, "List<OrderCreated>"), (7, "OrderCreated"),
    }
    # An export from an older script carries no evidence.
    assert parse_payload_types({"schema": "systemlens-joern-v1"}) == []
