"""Internal-flow shapes of the 2026-11-08 review (BACKLOG.md, Appendix A.2).

``tests/fixtures/internal_flow_shapes`` is a three-module Maven project:
``shared-lib`` declares ``Notifier`` and one implementation,
``plugin-lib`` implements it again on top of ``shared-lib``, and the
``orders-app`` application holds one entry per shape. Each shape is
indexed with the ``source`` engine, then rebuilt from the persisted index
with ``flows calculate``; both persisted flow sets are checked.

A case selects the flows of one entry, by entry method or trigger name,
and compares ``(confidence, steps after the trigger)``: the trigger label
of an entry kind not yet recognised is the decision of its item. A case
whose gap an undelivered backlog item owns is a strict expected failure
naming that item; delivering the item removes its ``item`` value. A case
without an item is a control that passes today.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import shutil

import pytest

from systemlens.application.flow_calculation import calculate_persisted_flows
from systemlens.callgraph.registry import declaration
from systemlens.domain.code_flows import CodeFlow
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.sqlite import Store

FIXTURE = Path(__file__).parent / "fixtures" / "internal_flow_shapes"
_ORDERS = "example.orders."

# (confidence, step names after the trigger)
Flow = tuple[str, tuple[str, ...]]


def _flow(confidence: str, *steps: str) -> Flow:
    # ``Type.method`` steps are methods of ``example.orders``; the others
    # are endpoint names.
    return confidence, tuple(f"{_ORDERS}{step}" if "." in step and " " not in step else step for step in steps)


@dataclass(frozen=True)
class FlowCase:
    id: str
    # The entry method, or the trigger step name of the flows.
    entry: str
    # The expected flows; ``None`` asks for at least one flow, when the
    # decision record of the item will define its shape.
    flows: frozenset[Flow] | None
    # The backlog item that owns the gap; ``None`` for a control.
    item: str | None = None


def _case(id: str, entry: str, item: str | None, *flows: Flow) -> FlowCase:
    return FlowCase(id, entry, frozenset(flows), item)


_TO_PUBLISHER = ("EventPublisher.publishProcessed", "order-processed")

CASES = [
    _case("control-service-client", f"{_ORDERS}OrderController.create", None,
          _flow("medium", "OrderService.create", "PaymentClient.charge", "POST /payments")),
    # S25: a job calling its own inherited template method reaches its own
    # override only, not the sibling CleanupJob's (ADR-43).
    _case("S25-own-template", f"{_ORDERS}AuditJob.trigger", None,
          _flow("low", "BaseJob.execute", "AuditJob.doRun", *_TO_PUBLISHER)),
    # The flow exists although extraction-rules.md excluded the trigger (BL-34).
    _case("scheduled-fixed-delay", f"{_ORDERS}MessagingEntries.tick", None,
          _flow("medium", *_TO_PUBLISHER)),
    # S24: FirstStep calls the template method on another object, a
    # SecondStep: the route leaves ``this``, so the entry-subtype rule no
    # longer holds and SecondStep's override is reached (ADR-43, BL-47).
    _case("S24-sibling-template", f"{_ORDERS}FirstStep.entry", None,
          _flow("low", "BaseStep.execute", "SecondStep.doStep", *_TO_PUBLISHER)),
    _case("inherited-crud-route", "POST /products/crud", "BL-48",
          _flow("medium", "PaymentClient.charge", "POST /payments")),
    _case("rabbit-listener", f"{_ORDERS}MessagingEntries.onRabbit", "BL-49",
          _flow("medium", *_TO_PUBLISHER)),
    _case("jms-listener", f"{_ORDERS}MessagingEntries.onJms", "BL-49",
          _flow("medium", *_TO_PUBLISHER)),
    _case("event-listener", f"{_ORDERS}OrderCreatedListener.onCreated", "BL-49",
          _flow("medium", *_TO_PUBLISHER)),
    _case("command-line-runner", f"{_ORDERS}StartupRunner.run", "BL-49",
          _flow("medium", *_TO_PUBLISHER)),
    FlowCase("dead-end", f"{_ORDERS}PingController.ping", None, "BL-50"),
    # s3, s4: MessageHandler<String> excludes BinaryHandler, a
    # MessageHandler<byte[]>; CodeQL dispatches exactly to TextHandler.
    _case("s3-generic-cast", f"{_ORDERS}HandlerController.registry", "BL-45",
          _flow("medium", "TextHandler.handle", *_TO_PUBLISHER)),
    _case("s4-generic-field", f"{_ORDERS}HandlerController.field", "BL-45",
          _flow("medium", "TextHandler.handle", *_TO_PUBLISHER)),
    # s19: the private helper's only call passes a CsvExporter.
    _case("s19-call-context", f"{_ORDERS}ExportController.helper", "BL-46",
          _flow("medium", "ExportController.exportWith", "CsvExporter.export", "export-csv")),
    # s17: the ternary holds a CsvExporter or a JsonExporter, never an
    # XmlExporter (CodeQL's rows).
    _case("s17-ternary", f"{_ORDERS}ExportController.ternary", "BL-46",
          _flow("low", "CsvExporter.export", "export-csv"),
          _flow("low", "JsonExporter.export", "export-json")),
]

COMMANDS = ("index", "flows calculate")


def _shapes(flows: list[CodeFlow]) -> list[tuple[str, str, str, tuple[str, ...]]]:
    return [
        (flow.method, flow.steps[0].name, flow.confidence, tuple(step.name for step in flow.steps[1:]))
        for flow in flows if flow.steps
    ]


def _index(root: Path, engine: str) -> dict[str, list[tuple[str, str, str, tuple[str, ...]]]]:
    repo = root / "repo"
    shutil.copytree(FIXTURE, repo)
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine=engine), store, full=True)
        indexed = _shapes(store.all_code_flows())
        with store.transaction():
            calculate_persisted_flows(store)
        calculated = _shapes(store.all_code_flows())
    return {"index": indexed, "flows calculate": calculated}


@pytest.fixture(scope="module")
def source_flows(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[tuple[str, str, str, tuple[str, ...]]]]:
    return _index(tmp_path_factory.mktemp("internal-flows-source"), "source")


def _param(case: FlowCase, command: str) -> object:
    marks = [pytest.mark.xfail(strict=True, reason=case.item)] if case.item else []
    return pytest.param(case, command, id=f"{case.id}-{command.replace(' ', '-')}", marks=marks)


@pytest.mark.parametrize(("case", "command"), [_param(case, command) for case in CASES for command in COMMANDS])
def test_source_engine_persists_the_shape_flows(
    source_flows: dict[str, list[tuple[str, str, str, tuple[str, ...]]]], case: FlowCase, command: str,
) -> None:
    got = {
        (confidence, steps) for method, trigger, confidence, steps in source_flows[command]
        if case.entry in (method, trigger)
    }
    if case.flows is None:
        assert got, f"{case.id}: no flow from {case.entry}"
    else:
        assert got == set(case.flows), (
            f"{case.id}: missing {sorted(set(case.flows) - got)}, extra {sorted(got - set(case.flows))}"
        )


def test_source_rebuild_reproduces_the_indexed_flows(
    source_flows: dict[str, list[tuple[str, str, str, tuple[str, ...]]]],
) -> None:
    # NoticeController.send reaches PluginNotifier, in a module orders-app
    # does not depend on, in both: the source pass crosses modules on a
    # source interface (BACKLOG.md, Appendix B.3, J2), fresh and rebuilt.
    assert sorted(source_flows["flows calculate"]) == sorted(source_flows["index"])


@pytest.fixture(scope="module")
def codeql_flows(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, list[tuple[str, str, str, tuple[str, ...]]]]]:
    if declaration("codeql").unavailable_reason() is not None:
        pytest.skip("Local CodeQL is required")
    yield _index(tmp_path_factory.mktemp("internal-flows-codeql"), "codeql")


@pytest.mark.slow
def test_codeql_rebuild_reproduces_the_indexed_flows(
    codeql_flows: dict[str, list[tuple[str, str, str, tuple[str, ...]]]],
) -> None:
    # ADR-71: the fresh join leaves out PluginNotifier, which orders-app
    # cannot see; the rebuild shares its expansion helper and leaves it out
    # too, with no POST /notices flow to plugin-notices (BL-51).
    assert not any(
        "plugin-notices" in steps for _method, _trigger, _confidence, steps in codeql_flows["flows calculate"]
    )
    assert sorted(codeql_flows["flows calculate"]) == sorted(codeql_flows["index"])


_TEMPLATE_CASES = [case for case in CASES if case.id in {"S24-sibling-template", "S25-own-template"}]


@pytest.mark.slow
@pytest.mark.parametrize(
    ("case", "command"),
    [_param(case, command) for case in _TEMPLATE_CASES for command in COMMANDS],
)
def test_codeql_engine_applies_the_entry_rule_only_on_this(
    codeql_flows: dict[str, list[tuple[str, str, str, tuple[str, ...]]]], case: FlowCase, command: str,
) -> None:
    # ADR-43 amendment (BL-47): S24 leaves ``this`` and reaches the sibling
    # override; S25 stays on ``this`` and keeps its own.
    test_source_engine_persists_the_shape_flows(codeql_flows, case, command)
