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

from systemlens.application.architecture_inventory import load_architecture_inventory
from systemlens.application.flow_calculation import calculate_persisted_flows
from systemlens.application.flow_diagnostic import diagnose_flows
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
    return confidence, tuple(
        f"{_ORDERS}{step}" if step[:1].isupper() and "." in step and " " not in step else step
        for step in steps
    )


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
    # ADR-88: the inherited method owns the route and the inheritor is the
    # entry class: ProductController's route never reaches the hook that
    # only its sibling UserController overrides.
    _case("inherited-crud-route", "POST /products/crud", None,
          _flow("medium", "PaymentClient.charge", "POST /payments")),
    _case("inherited-crud-route-sibling", "POST /users/crud", None,
          _flow("medium", "PaymentClient.charge", "POST /payments"),
          _flow("low", "UserController.afterCreate", *_TO_PUBLISHER)),
    # PDR-16: Rabbit and JMS listeners are consume endpoints; event
    # listeners and startup code are transient triggers.
    _case("rabbit-listener", f"{_ORDERS}MessagingEntries.onRabbit", None,
          _flow("medium", *_TO_PUBLISHER)),
    _case("jms-listener", f"{_ORDERS}MessagingEntries.onJms", None,
          _flow("medium", *_TO_PUBLISHER)),
    _case("event-listener", f"{_ORDERS}OrderCreatedListener.onCreated", None,
          _flow("medium", *_TO_PUBLISHER)),
    _case("transactional-event-listener", f"{_ORDERS}AuditListener.audit", None,
          _flow("medium", "audit-events")),
    _case("command-line-runner", f"{_ORDERS}StartupRunner.run", None,
          _flow("medium", *_TO_PUBLISHER)),
    _case("application-runner", f"{_ORDERS}CacheWarmer.run", None,
          _flow("medium", *_TO_PUBLISHER)),
    _case("post-construct", f"{_ORDERS}CacheWarmer.warm", None,
          _flow("medium", *_TO_PUBLISHER)),
    # PDR-16: publishEvent continues the flow into every listener of the
    # event's static type, through a possible edge.
    _case("event-publication", f"{_ORDERS}EventController.create", None,
          _flow("low", "OrderCreatedListener.onCreated", *_TO_PUBLISHER),
          _flow("low", "AuditListener.audit", "audit-events")),
    # PDR-15: Rabbit and JMS publications are effects. The default
    # exchange names the queue; a named exchange keeps exchange/routing-key.
    _case("rabbit-publication", f"{_ORDERS}BrokerController.rabbit", None,
          _flow("medium", "rabbit-in")),
    _case("rabbit-exchange-publication", f"{_ORDERS}BrokerController.exchange", None,
          _flow("medium", "orders-exchange/orders.created")),
    _case("jms-publication", f"{_ORDERS}BrokerController.jms", None,
          _flow("medium", "jms-in")),
    # PDR-15: a route that reaches no effect is not a flow (BL-50).
    _case("dead-end", f"{_ORDERS}PingController.ping", None),
    # s3, s4: MessageHandler<String> excludes BinaryHandler, a
    # MessageHandler<byte[]>; CodeQL dispatches exactly to TextHandler.
    _case("s3-generic-cast", f"{_ORDERS}HandlerController.registry", None,
          _flow("medium", "TextHandler.handle", *_TO_PUBLISHER)),
    _case("s4-generic-field", f"{_ORDERS}HandlerController.field", None,
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


def test_dead_end_route_is_diagnosed_and_not_persisted(
    source_flows: dict[str, list[tuple[str, str, str, tuple[str, ...]]]], tmp_path: Path,
) -> None:
    # PDR-15 (BL-50): GET /ping reaches no effect. No flow is persisted and
    # the flow diagnostic names the route as an entry without an effect.
    assert not [flow for flow in source_flows["index"] if flow[1] == "GET /ping"]
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="source"), store, full=True)
    entries = {
        entry["entry"]: entry["status"]
        for entry in diagnose_flows(load_architecture_inventory(repo))["entries"]
    }
    assert entries["GET /ping"] == "entry_method_without_external_effect"
    # A route with a local flow and no paired consumer.
    assert entries["POST /broker/rabbit"] == "external_relation_unresolved"


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


def test_index_log_reports_the_entries_stopped_at_the_hop_limit(tmp_path: Path) -> None:
    # BL-52: the index log names the setting and counts truncated entries.
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    for max_hops, truncated in ((12, False), (1, True)):
        progress: list[str] = []
        with Store(repo) as store:
            index_repo(
                repo, Config(call_graph_engine="source", engine_max_hops=max_hops), store,
                full=True, progress=progress.append,
            )
        lines = [line for line in progress if f"à la limite engine_max_hops={max_hops}" in line]
        assert len(lines) == 1, progress
        count = int(lines[0].split(" : ", 1)[1].split(" ", 1)[0])
        assert (count > 0) is truncated
