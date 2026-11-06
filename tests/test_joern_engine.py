"""The Joern engine adapter (ADR-85).

The unit tests decode a frozen export of ``tests/fixtures/joern/repo``
produced by ``engines/joern/export.sc`` with Joern 4.0.674, so they need
no Joern. The ``slow`` test runs the real engine when Joern is installed.
"""

import json
import shutil
from pathlib import Path

import pytest

from systemlens.callgraph.engine import EngineRequest, EngineSettings
from systemlens.callgraph.model import MethodCall
from systemlens.callgraph.registry import declaration, load_engine
from systemlens.engines.joern import engine as joern_engine
from systemlens.engines.joern.extraction import JoernError, parse_calls, parse_methods
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.sqlite import Store

FIXTURES = Path(__file__).parent / "fixtures" / "joern"
CONTROLLER = "src/main/java/example/OrderController.java"


def _export() -> dict[str, object]:
    return json.loads((FIXTURES / "export.json").read_text(encoding="utf-8"))


def _edges(calls: list[MethodCall]) -> set[tuple[str, str, int, str]]:
    return {
        (call.caller, call.callee, call.call_line, call.dispatch_confidence) for call in calls
    }


def test_joern_calls_keep_only_resolved_source_targets() -> None:
    calls = parse_calls(_export())

    assert _edges(calls) == {
        ("example.OrderController.<init>", "example.OrderController.init", 11, "exact"),
        # Two implementations: every target stays possible, the interface
        # declaration included.
        ("example.OrderController.create", "example.OrderService.place", 17, "possible"),
        ("example.OrderController.create", "example.OrderServiceImpl.place", 17, "possible"),
        ("example.OrderController.create", "example.OtherServiceImpl.place", 17, "possible"),
        # A unique implementation is exact; its interface stays a witness.
        ("example.OrderController.create", "example.Repo.save", 18, "possible"),
        ("example.OrderController.create", "example.RepoImpl.save", 18, "exact"),
        # Lambda and anonymous-class bodies belong to the enclosing method.
        ("<anonymous>", "example.OrderController.helper", 19, "exact"),
        ("<anonymous>", "example.OrderController.helper", 20, "exact"),
        ("example.OrderController.create", "example.OrderController$Nested.go", 22, "exact"),
        ("example.OrderController.helper", "example.Util.log", 25, "exact"),
        ("example.OrderServiceImpl.place", "example.Util.log", 2, "exact"),
    }
    # The unresolved ``unknown.Thing.call()``, library calls and
    # constructor targets produce no edge.
    assert not any("unresolved" in call.callee or call.callee.startswith("java.") for call in calls)
    assert not any(call.callee.endswith("<init>") for call in calls)
    create = next(call for call in calls if call.callee == "example.RepoImpl.save")
    assert (create.caller_path, create.caller_line, create.callee_line) == (CONTROLLER, 16, 2)
    assert create.caller_parameter_types == ("java.lang.String",)
    assert create.callee_parameter_types == ("java.lang.String",)


def test_an_overridden_interface_method_is_no_witness_of_a_concrete_call() -> None:
    """Joern links ``this.find()`` in an implementation to its interface too."""
    def method(name: str, line: int, abstract: bool) -> dict[str, object]:
        return {
            "full_name": f"{name}:void()", "file": "src/Svc.java", "line": line,
            "abstract": abstract, "external": False,
        }

    document = {
        "schema": "systemlens-joern-v1",
        "methods": [],
        "calls": [{
            "caller": "app.SvcImpl.modify:void()", "caller_file": "src/Svc.java",
            "caller_line": 10, "line": 12, "target": "app.SvcImpl.find:void()",
            "dispatch": "DYNAMIC_DISPATCH",
            "callees": [method("app.SvcImpl.find", 20, False), method("app.Svc.find", 3, True)],
        }],
    }

    assert _edges(parse_calls(document)) == {
        ("app.SvcImpl.modify", "app.SvcImpl.find", 12, "exact"),
    }


def test_joern_calls_and_methods_stay_inside_the_indexed_files() -> None:
    indexed = [CONTROLLER, "src/main/java/example/Util.java"]

    calls = parse_calls(_export(), indexed)
    methods = parse_methods(_export(), indexed)

    assert {call.caller_path for call in calls} == {CONTROLLER}
    assert {call.callee_path for call in calls} <= set(indexed)
    assert ("example.OrderController.helper", "example.Util.log", 25, "exact") in _edges(calls)
    assert [method.qualified_method for method in methods] == [
        "example.OrderController.init",
        "example.OrderController.create",
        "example.OrderController.helper",
        "example.OrderController$Nested.go",
        "example.Util.log",
    ]
    assert parse_methods(_export(), source_prefix="src/main/java/example/Util") == []


def test_an_unknown_export_schema_is_an_engine_error() -> None:
    with pytest.raises(JoernError, match="schema"):
        parse_calls({"schema": "other", "calls": []})
    with pytest.raises(JoernError, match="unexpected call schema"):
        parse_calls({"schema": "systemlens-joern-v1", "calls": [{"line": 3}]})


def test_joern_declaration_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    item = declaration("joern")
    assert (item.supports_module_refresh, item.resolves_types, item.database_option) == (
        False, True, None,
    )
    engine = load_engine("joern", EngineSettings(threads=3, ram_mb=2048))
    assert isinstance(engine, joern_engine.JoernEngine)
    assert joern_engine._jvm_environment(3, 2048)["JAVA_OPTS"].endswith(
        "-Xmx2048m -XX:ActiveProcessorCount=3"
    )
    monkeypatch.setattr(joern_engine, "joern_version", lambda: "4.0.674")
    assert engine.input_fingerprint() == "joern-4.0.674"


@pytest.mark.slow
@pytest.mark.skipif(
    shutil.which("joern") is None or declaration("joern").unavailable_reason() is not None,
    reason="Joern is not installed or its launcher prerequisites are missing",
)
def test_joern_engine_builds_and_caches_a_cpg(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "repo", repo)
    java = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*.java"))
    engine = load_engine("joern")
    assert engine is not None
    request = EngineRequest(repo_root=repo, allowed_paths=java, timeout_seconds=600)

    with engine.open(request) as session:
        calls = session.calls()
        methods = session.methods()

    assert _edges(calls) == _edges(parse_calls(_export()))
    assert len(methods) == 10
    cache = repo / ".systemlens" / "joern"
    assert (cache / "cpg.bin").is_file() and (cache / "cpg.fingerprint").is_file()

    messages: list[str] = []
    with engine.open(EngineRequest(
        repo_root=repo, allowed_paths=java, timeout_seconds=600,
        progress=messages.append, verbose=True,
    )) as session:
        assert _edges(session.calls()) == _edges(calls)
    assert any("CPG réutilisé" in message for message in messages)


def _row(name: str, line: int, *, abstract: bool = False, external: bool = False,
         file: str = "src/App.java") -> dict[str, object]:
    return {
        "full_name": name, "file": file, "line": line,
        "abstract": abstract, "external": external,
    }


def _document(*calls: dict[str, object]) -> dict[str, object]:
    return {"schema": "systemlens-joern-v1", "methods": [], "calls": list(calls)}


def test_a_local_class_method_takes_the_index_spelling() -> None:
    """Review A2: Joern spells ``App.create:void().Local.go:void()``.

    Cutting at the first ``:`` named ``Local.go`` after ``App.create``; the
    join then added a self-edge and a ``cycle`` flow. The index spells the
    method ``App.Local.go``, without the enclosing method.
    """
    local = "example.App.create:void().Local.go:void()"
    document = _document(
        {
            "caller": "example.App.create:void()", "caller_file": "src/App.java",
            "caller_line": 6, "line": 10, "target": "example.App.create:void().Local.<init>:void()",
            "dispatch": "STATIC_DISPATCH",
            "callees": [_row("example.App.create:void().Local.<init>:void()", 7)],
        },
        {
            "caller": "example.App.create:void()", "caller_file": "src/App.java",
            "caller_line": 6, "line": 10, "target": local, "dispatch": "DYNAMIC_DISPATCH",
            "callees": [_row(local, 8)],
        },
        {
            "caller": local, "caller_file": "src/App.java", "caller_line": 8, "line": 8,
            "target": "example.Util.log:void()", "dispatch": "STATIC_DISPATCH",
            "callees": [_row("example.Util.log:void()", 2, file="src/Util.java")],
        },
    )
    document["methods"] = [
        _row("example.App.create:void()", 6), _row(local, 8),
        _row("example.App.create:void().Local.<init>:void()", 7),
    ]

    assert _edges(parse_calls(document)) == {
        ("example.App.create", "example.App.Local.go", 10, "exact"),
        ("example.App.Local.go", "example.Util.log", 8, "exact"),
    }
    assert [method.qualified_method for method in parse_methods(document)] == [
        "example.App.create", "example.App.Local.go",
    ]


def test_a_dropped_anonymous_or_library_candidate_keeps_the_dispatch_possible() -> None:
    """Review A3: candidates the output cannot carry still receive the call.

    Joern lists an anonymous-class body and a library class among the
    targets of ``task.run()``; the one named source body is no longer the
    only body, so it stays ``possible`` (ADR-70, ADR-81).
    """
    external = _document({
        "caller": "app.Runner.start:void()", "caller_file": "src/App.java",
        "caller_line": 10, "line": 12, "target": "java.lang.Runnable.run:void()",
        "dispatch": "DYNAMIC_DISPATCH",
        "callees": [
            _row("java.lang.Runnable.run:void()", 1, abstract=True, external=True, file=""),
            _row("app.Runner$0.run:void()", 30),
            _row("java.util.concurrent.FutureTask.run:void()", 1, external=True, file=""),
            _row("app.Worker.run:void()", 40),
        ],
    })
    assert _edges(parse_calls(external)) == {("app.Runner.start", "app.Worker.run", 12, "possible")}

    source = _document({
        "caller": "example.App.create:void()", "caller_file": "src/App.java",
        "caller_line": 6, "line": 11, "target": "example.Port.run:void()",
        "dispatch": "DYNAMIC_DISPATCH",
        "callees": [
            _row("example.Port.run:void()", 2, abstract=True),
            _row("example.Adapter.run:void()", 3),
            _row("example.App.install.Port$0.run:void()", 14),
        ],
    })
    assert _edges(parse_calls(source)) == {
        ("example.App.create", "example.Adapter.run", 11, "possible"),
        ("example.App.create", "example.Port.run", 11, "possible"),
    }


def test_joern_parse_keeps_lombok_sources_and_rebuilds_an_older_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review J1: ``types-only`` delombok, and the options in the fingerprint."""
    commands: list[list[str]] = []

    def run(command: list[str], **_: object) -> object:
        commands.append(command)
        Path(command[command.index("-o") + 1]).write_bytes(b"cpg")
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(joern_engine, "run_with_progress", run)
    monkeypatch.setattr(joern_engine, "joern_version", lambda: "4.0.674")
    source = tmp_path / "source"
    (source / "m").mkdir(parents=True)
    (source / "m" / "A.java").write_text("package m; class A {}", encoding="utf-8")
    cache = tmp_path / joern_engine.CACHE_DIRECTORY
    cache.mkdir(parents=True)
    (cache / "cpg.bin").write_bytes(b"old")
    # The fingerprint an engine without parse options wrote.
    (cache / "cpg.fingerprint").write_text(
        joern_engine.projection_fingerprint(source, "joern-parse javasrc\n4.0.674\n") + "\n",
        encoding="utf-8",
    )
    engine = joern_engine.JoernEngine()
    request = EngineRequest(repo_root=tmp_path, allowed_paths=["m/A.java"], timeout_seconds=60)

    engine._cpg(request, source, tmp_path, {}, None)
    engine._cpg(request, source, tmp_path, {}, None)

    assert len(commands) == 1
    assert commands[0][-3:] == ["--frontend-args", "--delombok-mode", "types-only"]
    assert (cache / "cpg.bin").read_bytes() == b"cpg"


_JOERN = pytest.mark.skipif(
    shutil.which("joern") is None or declaration("joern").unavailable_reason() is not None,
    reason="Joern is not installed or its launcher prerequisites are missing",
)


@pytest.mark.slow
@_JOERN
def test_joern_lines_name_the_written_lombok_source(tmp_path: Path) -> None:
    """Review J1: no method or call line beyond the indexed file's end.

    With the default delombok mode, ``User.getAge`` was reported at line 23
    of a 15-line file and ``setName`` at line 29.
    """
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "lombok", repo)
    java = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*.java"))
    engine = load_engine("joern")
    assert engine is not None

    with engine.open(EngineRequest(repo_root=repo, allowed_paths=java, timeout_seconds=600)) as session:
        calls = session.calls()
        methods = session.methods()

    length = {path: len((repo / path).read_text(encoding="utf-8").splitlines()) for path in java}
    assert {(method.qualified_method, method.start_line) for method in methods} == {
        ("m.Use.go", 3), ("m.User.getName", 8), ("m.User.describe", 12),
    }
    assert all(call.callee_line <= length[call.callee_path] for call in calls)
    assert _edges(calls) == {
        ("m.Use.go", "m.User.getName", 5, "exact"),
        ("m.Use.go", "m.User.describe", 5, "exact"),
        ("m.User.describe", "m.User.getName", 13, "exact"),
    }


@pytest.mark.slow
@_JOERN
def test_a_local_class_call_joins_its_method_without_a_self_edge(tmp_path: Path) -> None:
    """Review A2 through ``index_repo``: no ``create → create`` edge, no cycle."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "local-class", repo)

    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="joern"), store, full=True)
        names = {method.id: method.qualified_method for method in store.all_call_graph_methods()}
        for method in store.all_integration_methods():
            names.setdefault(method.id, method.qualified_method)
        edges = {
            (names.get(edge.caller_id), names.get(edge.callee_id), edge.line)
            for edge in store.all_call_edges()
        }
        flows = store.all_code_flows()

    assert ("example.OrderController.create", "example.OrderController.Local.go", 13) in edges
    assert not any(caller == callee for caller, callee, _ in edges)
    assert {flow.status for flow in flows} == {"potential"}
    assert [[step.name for step in flow.steps] for flow in flows] == [[
        "POST /orders/generate", "example.OrderController.Local.go",
        "example.Generator.generate", "orders",
    ]]
