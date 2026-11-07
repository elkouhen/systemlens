"""Constructors and initializers in the call graph (ADR-75, review J5).

``tests/fixtures/constructors`` routes a REST input to a Kafka output
through a constructor: ``create`` runs ``new Publisher(...)``, whose
constructor calls ``publish``. Field initializers, a delegating
constructor, an implicit constructor and static initializer code give the
other node kinds. Every engine must build the same constructor edges.
"""

import shutil
from pathlib import Path

import pytest

from systemlens.callgraph.registry import declaration
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.sqlite import Store

FIXTURE = Path(__file__).parent / "fixtures" / "constructors"
PUBLISHER = "src/main/java/example/Publisher.java"

# (caller, callee, call line, confidence) between indexed nodes.
CONSTRUCTOR_EDGES = {
    ("example.OrderController.create", "example.Publisher.<init>", 15, "exact"),
    # The field initializer runs in the constructor that does not delegate.
    ("example.Publisher.<init>", "example.Audit.<init>", 6, "exact"),
    ("example.Publisher.<init>", "example.Publisher.publish", 10, "exact"),
    ("example.Publisher.<init>", "example.Publisher.<init>", 14, "exact"),
    ("example.Publisher.<clinit>", "example.Limits.limit", 7, "exact"),
    # ``Audit`` declares no constructor: its initializer makes it one.
    ("example.Audit.<init>", "example.Ledger.open", 4, "exact"),
}


def _module(root: Path) -> DiscoveredModule:
    return DiscoveredModule(
        name="orders", path=root, build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )


def test_constructors_and_initializers_are_nodes_without_ports(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    paths = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*.java"))

    methods = materialize_integration_methods(repo, [], paths, [_module(repo)])

    nodes = {
        (method.qualified_method, method.start_line, method.end_line, method.parameter_types)
        for method in methods if "<" in method.qualified_method
    }
    assert nodes == {
        ("example.Publisher.<init>", 9, 11, ("KafkaTemplate<String, String>",)),
        ("example.Publisher.<init>", 13, 15, ()),
        # Implicit nodes stand on the type's header.
        ("example.Publisher.<clinit>", 5, 5, ()),
        ("example.Audit.<init>", 3, 3, ()),
    }
    # ``Ledger`` has neither a constructor nor initializer code.
    assert not any(method.qualified_method.startswith("example.Ledger.<") for method in methods)
    assert len({method.id for method in methods}) == len(methods)


def _index(tmp_path: Path, engine: str) -> tuple[set[tuple[str, str, int, str]], list[list[str]]]:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine=engine), store, full=True)
        names = {method.id: method.qualified_method for method in store.all_call_graph_methods()}
        for method in store.all_integration_methods():
            names.setdefault(method.id, method.qualified_method)
        edges = {
            (names[edge.caller_id], names[edge.callee_id], edge.line, edge.dispatch_confidence)
            for edge in store.all_call_edges()
        }
        flows = [[step.name for step in flow.steps] for flow in store.all_code_flows()]
    return edges, flows


def _available(engine: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        declaration(engine).unavailable_reason() is not None,
        reason=f"{engine} is not installed",
    )


@pytest.mark.parametrize("engine", [
    "source",
    pytest.param("codeql", marks=[pytest.mark.slow, _available("codeql")]),
    pytest.param("joern", marks=[pytest.mark.slow, _available("joern")]),
])
def test_every_engine_routes_a_flow_through_a_constructor(tmp_path: Path, engine: str) -> None:
    edges, flows = _index(tmp_path, engine)

    assert {
        edge for edge in edges if "<" in edge[0] or "<" in edge[1]
    } == CONSTRUCTOR_EDGES
    # A default constructor the index does not hold is no placeholder.
    assert not any(callee.startswith("example.Ledger.<") for _, callee, _, _ in edges)
    assert flows == [[
        "POST /orders/create", "example.Publisher.<init>", "example.Publisher.publish", "orders",
    ]]
