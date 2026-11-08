"""Regressions of flow construction (Copilot backlog, Lot 4).

Each test pins a finding of ``backlog-copilot.md`` on a small Spring
fixture indexed with the ``source`` engine, or on the join directly.
"""

from dataclasses import replace
from pathlib import Path

from systemlens.callgraph.model import MethodCall
from systemlens.domain.code_flows import (
    CallGraphEdge,
    CodeFlow,
    CodeFlowStep,
    IntegrationMethod,
)
from systemlens.domain.models import MessageEndpoint
from systemlens.indexing.code_flows import (
    _deduplicate_code_flows,
    materialize_call_graph_flows,
)
from systemlens.indexing.java_value_origins import trace_scalar_key_identity
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


def _entities() -> dict[str, str]:
    files = {}
    for entity in ("Order", "Item"):
        files[f"{PACKAGE}/{entity}.java"] = (
            "package example.app;\nimport jakarta.persistence.*;\n"
            f"@Entity\npublic class {entity} {{ @Id Long id; }}\n"
        )
        files[f"{PACKAGE}/{entity}Repo.java"] = (
            "package example.app;\n"
            "import org.springframework.data.jpa.repository.JpaRepository;\n"
            f"public interface {entity}Repo extends JpaRepository<{entity}, Long> {{}}\n"
        )
    return files


def _read_service(body: str) -> dict[str, str]:
    return {
        **_entities(),
        f"{PACKAGE}/ReadController.java": """package example.app;
import org.springframework.web.bind.annotation.*;
@RestController
public class ReadController {
    private final ReadService service;
    public ReadController(ReadService service) { this.service = service; }
    @GetMapping("/read")
    public void read(Long x, Long y) {
        service.read(x, y);
    }
}
""",
        f"{PACKAGE}/ReadService.java": f"""package example.app;
import org.springframework.stereotype.Service;
@Service
public class ReadService {{
    private final OrderRepo orders;
    private final ItemRepo items;
    public ReadService(OrderRepo orders, ItemRepo items) {{ this.orders = orders; this.items = items; }}
    public void read(Long x, Long y) {{
{body}
    }}
}}
""",
    }


def _data_flows(flows: list[CodeFlow]) -> dict[str, tuple[str, int]]:
    return {
        flow.steps[-1].name: (flow.id, flow.steps[-1].start_line)
        for flow in flows if flow.steps[-1].kind == "data_read"
    }


def test_two_operations_on_one_line_are_two_flows(tmp_path: Path) -> None:
    """BC-25: data steps are keyed by operation, not by source line.

    ``orders.findById(x); items.findById(y);`` share a line and an
    operation name, so the join kept one flow and lost the ``Item`` read.
    """
    repo = _write(tmp_path / "repo", _read_service(
        "        orders.findById(x); items.findById(y);"
    ))

    flows = _data_flows(_index(repo))

    assert sorted(flows) == ["Item", "Order"]
    assert flows["Item"][0] != flows["Order"][0]


def test_a_data_flow_id_survives_line_movement(tmp_path: Path) -> None:
    """BC-25: ``compute_code_flow_id`` promises IDs that survive line movement.

    The data-flow coordinate embedded the operation's line, so moving a
    repository call by one line gave the flow a new ID.
    """
    before = _data_flows(_index(_write(tmp_path / "a", _read_service(
        "        orders.findById(x);"
    ))))
    after = _data_flows(_index(_write(tmp_path / "b", _read_service(
        "\n        orders.findById(x);"
    ))))

    assert after["Order"][1] == before["Order"][1] + 1
    assert after["Order"][0] == before["Order"][0]

def test_twin_operations_keep_distinct_ids_across_line_movement(tmp_path: Path) -> None:
    """BC-25: twins, one call twice in a method, are numbered by their order.

    Without the line, both twins had one coordinate. The collision suffix
    went to whichever came second in operation-ID order, which follows
    the lines, so moving a line could swap the two IDs.
    """
    def ids(repo: Path, body: str) -> list[tuple[int, str]]:
        flows = _index(_write(repo, _read_service(body)))
        return sorted(
            (flow.steps[-1].start_line, flow.id)
            for flow in flows if flow.steps[-1].kind == "data_read"
        )

    before = ids(tmp_path / "a", "        orders.findById(x);\n        orders.findById(x);")
    after = ids(tmp_path / "b", "\n        orders.findById(x);\n        orders.findById(x);")

    assert [line + 1 for line, _id in before] == [line for line, _id in after]
    assert [flow_id for _line, flow_id in before] == [flow_id for _line, flow_id in after]
    assert len({flow_id for _line, flow_id in before}) == 2
    assert not any("-" in flow_id for _line, flow_id in before)



ORDER = "jpa:orders:example.Order"


def test_a_local_declared_after_the_operation_is_not_its_key(tmp_path: Path) -> None:
    """BC-26: the usage line is the operation's line, not the method's end.

    A ``final String key = "x"`` declared after ``findById(key)`` shadowed
    the field ``key`` and gave the read a literal identity.
    """
    path = "src/Adapter.java"
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(
        "package example; class Adapter {\n"
        "  String key;\n"
        "  void load() {\n"
        "    repository.findById(key);\n"
        "    final String key = \"x\";\n"
        "    System.out.println(key);\n"
        "  }\n}\n",
        encoding="utf-8",
    )
    method = IntegrationMethod("m", "orders", "example.Adapter.load", path, 3, 7, (), ())

    assert trace_scalar_key_identity(
        tmp_path, [], method, method, "key", ORDER, usage_line=4,
    ) is None
    assert trace_scalar_key_identity(
        tmp_path, [], method, method, "key", ORDER, usage_line=6,
    ) == f"{ORDER}:literal:\"x\""


def test_a_nested_engine_caller_traces_its_key(tmp_path: Path) -> None:
    """BC-26: an engine names a nested caller ``Entry$In.handle``.

    The raw name was compared with the dotted source name, so the key was
    traced from the source engine's rows and not from CodeQL's.
    """
    entry_path, adapter_path = "src/Entry.java", "src/Adapter.java"
    (tmp_path / "src").mkdir()
    (tmp_path / entry_path).write_text(
        "package example; class Entry { static class In { void handle(String id) { "
        "new Adapter().load(id); } } }\n",
        encoding="utf-8",
    )
    (tmp_path / adapter_path).write_text(
        "package example; class Adapter { void load(String key) { "
        "repository.findById(key); } }\n",
        encoding="utf-8",
    )
    entry = IntegrationMethod("entry", "orders", "example.Entry.In.handle", entry_path, 1, 1, (), ())
    adapter = IntegrationMethod("ad", "orders", "example.Adapter.load", adapter_path, 1, 1, (), ())
    identities = {
        trace_scalar_key_identity(
            tmp_path,
            [(adapter, MethodCall(
                caller=caller, caller_path=entry_path, caller_line=1,
                callee=adapter.qualified_method, callee_path=adapter_path,
                callee_line=1, call_line=1,
            ), False)],
            entry, adapter, "key", ORDER, usage_line=1,
        )
        for caller in ("example.Entry.In.handle", "example.Entry$In.handle")
    }

    assert identities == {f"{ORDER}:parameter:entry:0"}


def _listener(phase: str) -> dict[str, str]:
    return {f"{PACKAGE}/Relay.java": f"""package example.app;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;
import static org.springframework.transaction.event.TransactionPhase.AFTER_COMPLETION;
@Component
public class Relay {{
    private final KafkaTemplate<String, String> kafkaTemplate;
    public Relay(KafkaTemplate<String, String> t) {{ this.kafkaTemplate = t; }}
    @TransactionalEventListener{phase}
    public void relay(String e) {{
        kafkaTemplate.send("relay-out", e);
    }}
}}
"""}


def test_only_a_post_commit_listener_phase_marks_after_commit(tmp_path: Path) -> None:
    """BC-27, PDR-17: the listener's ``phase`` decides the ``after_commit`` mark.

    A publication in an ``AFTER_ROLLBACK`` listener was persisted as
    ``after_commit``, which downgraded a dual-write candidate.
    """
    phases = {
        "": "after_commit",
        "(phase = TransactionPhase.AFTER_COMMIT)": "after_commit",
        "(phase = AFTER_COMPLETION)": "after_commit",
        "(phase = TransactionPhase.AFTER_ROLLBACK)": None,
        "(phase = TransactionPhase.BEFORE_COMMIT)": None,
        "(classes = String.class, phase = Phases.CUSTOM)": None,
    }
    for index, (phase, expected) in enumerate(phases.items()):
        flows = _index(_write(tmp_path / f"repo{index}", _listener(phase)))

        assert [flow.steps[-1].phase for flow in flows] == [expected], phase


def test_the_reverse_call_chain_walk_is_bounded_on_fan_in() -> None:
    """BC-28: the ``--show-call-chains`` walk keeps one route per (caller, depth).

    Checking cycles per route only enumerated every simple path: twelve
    fully connected layers of three callers printed 797,211 lines.
    """
    output = IntegrationMethod("out", "app", "p.Out.send", "Out.java", 1, 3, (), ("o",))
    methods, edges, previous = [output], [], [output]
    layers, width = 12, 3
    for layer in range(layers):
        current = [
            IntegrationMethod(
                f"m{layer}_{index}", "app", f"p.L{layer}_{index}.run",
                f"L{layer}_{index}.java", 1, 3, (), (),
            )
            for index in range(width)
        ]
        methods += current
        edges += [
            CallGraphEdge(caller.id, callee.id, caller.path, 2, "exact")
            for caller in current for callee in previous
        ]
        previous = current
    endpoint = MessageEndpoint(
        "o", "produce", "kafka", "t", False, "code", "test", "Out.java", 2, 2, "t",
        module="app",
    )
    lines: list[str] = []

    materialize_call_graph_flows(
        methods, [endpoint], [], persisted_edges=edges,
        call_chain_progress=lines.append, call_chain_module="app",
    )

    explored = [line for line in lines if "Chaîne explorée" in line]
    assert len(explored) == 1 + layers * width
    assert any("[arrêt profondeur=12 : limite de profondeur]" in line for line in lines)
