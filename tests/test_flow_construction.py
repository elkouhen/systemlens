"""Regressions of flow construction (Copilot backlog, Lot 4).

Each test pins a finding of ``backlog-copilot.md`` on a small Spring
fixture indexed with the ``source`` engine, or on the join directly.
"""

from dataclasses import replace
from pathlib import Path

from systemlens.domain.code_flows import CodeFlow, CodeFlowStep
from systemlens.indexing.code_flows import (
    _deduplicate_code_flows,
    materialize_call_graph_flows,
)
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.sqlite import Store

POM = (
    "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
    "<artifactId>app</artifactId><version>1.0</version></project>"
)
PACKAGE = "src/main/java/example/app"


def _write(root: Path, files: dict[str, str]) -> Path:
    for relative, text in {"pom.xml": POM, **files}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _index(repo: Path) -> list[CodeFlow]:
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="source"), store, full=True)
        return store.all_code_flows()


def _publisher(name: str) -> str:
    return f"""package example.app;
import org.springframework.stereotype.Component;
@Component
public class {name} {{
    private final Publisher publisher;
    public {name}(Publisher publisher) {{ this.publisher = publisher; }}
    public void go(String m) {{
        publisher.publish(m);
    }}
}}
"""


DIAMOND = {
    f"{PACKAGE}/DiamondController.java": """package example.app;
import org.springframework.web.bind.annotation.*;
@RestController
public class DiamondController {
    private final B b;
    private final C c;
    public DiamondController(B b, C c) { this.b = b; this.c = c; }
    @PostMapping("/diamond")
    public void create(@RequestBody String body) {
        b.go(body);
        c.go(body);
    }
}
""",
    f"{PACKAGE}/B.java": _publisher("B"),
    f"{PACKAGE}/C.java": _publisher("C"),
    f"{PACKAGE}/Publisher.java": """package example.app;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
@Component
public class Publisher {
    private final KafkaTemplate<String, String> kafkaTemplate;
    public Publisher(KafkaTemplate<String, String> t) { this.kafkaTemplate = t; }
    public void publish(String m) {
        kafkaTemplate.send("diamond-out", m);
    }
}
""",
}


def test_a_diamond_persists_its_route_count(tmp_path: Path) -> None:
    """BC-24: ``A -> B -> D`` and ``A -> C -> D`` are two routes of one flow.

    The final deduplication recounted the routes of the one flow the join
    kept per group, so the diamond was persisted with one alternative.
    """
    flows = _index(_write(tmp_path / "repo", DIAMOND))

    assert [(flow.method, flow.alternative_count) for flow in flows] == [
        ("example.app.DiamondController.create", 2),
    ]


def _flow(flow_id: str, count: int) -> CodeFlow:
    return CodeFlow(
        id=flow_id, module="app", method="example.app.A.run", path="A.java",
        start_line=1, end_line=3, status="potential", confidence="medium",
        reason="test", alternative_count=count,
        steps=(
            CodeFlowStep(1, "http_entry", "GET /a", "A.java", 1, 1, endpoint_id="in"),
            CodeFlowStep(2, "message_publish", "out", "A.java", 2, 2, endpoint_id="out"),
        ),
    )


def test_the_final_deduplication_keeps_the_counted_routes() -> None:
    """BC-24: the final deduplication takes the largest incoming count."""
    assert [flow.alternative_count for flow in _deduplicate_code_flows([_flow("f", 3)])] == [3]
    assert [
        flow.alternative_count
        for flow in _deduplicate_code_flows([_flow("f", 2), replace(_flow("g", 1), start_line=2)])
    ] == [2]


def test_a_seeded_flow_keeps_its_route_count() -> None:
    """BC-24: a resumed join seeds its checkpoint without resetting counts."""
    flows = materialize_call_graph_flows([], [], [], initial_flows=[_flow("f", 3)])

    assert [flow.alternative_count for flow in flows] == [3]
