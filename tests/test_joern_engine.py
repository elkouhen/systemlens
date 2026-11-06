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
