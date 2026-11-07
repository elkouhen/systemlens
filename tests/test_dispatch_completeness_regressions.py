"""Regressions of ADR-81 (backlog items BL-01 and BL-32).

The ``source`` engine runs in the default suite; CodeQL and Joern run the
rule 1 fixtures in ``slow`` tests, skipped without their CLI.

A call is ``exact`` only when every implementor of the receiver has a
known body and exactly one applies. Each test names its finding in the
2026-11-04 call-graph review (``docs/plans/call-graph-review-2026-11-04.md``
in Git at ``4a12ad5``).
"""
import shutil
from pathlib import Path

import pytest

from systemlens.callgraph.engine import EngineRequest
from systemlens.callgraph.registry import declaration, load_engine
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.engines.codeql.database import codeql_executable
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing.java_symbols import JavaSymbols

_PATH = "app/src/main/java/p/Svc.java"


def _calls(tmp_path: Path, source: str, caller: str = "p.Svc.run") -> list[tuple[str, str]]:
    (tmp_path / _PATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / _PATH).write_text(source, encoding="utf-8")
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(tmp_path, [], [_PATH], [module])
    symbols = JavaSymbols(tmp_path, methods, [_PATH])
    return sorted(
        (call.callee, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller == caller
    )


def _engine_calls(
    tmp_path: Path, engine_name: str, source: str, caller: str = "p.Svc.run",
) -> list[tuple[str, str]]:
    """The rows a typed engine gives from ``caller`` to indexed methods.

    CodeQL also reports Lombok-generated members, which no engine indexes:
    the join drops them, as it drops them here.
    """
    (tmp_path / _PATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / _PATH).write_text(source, encoding="utf-8")
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    indexed = {
        (method.path, method.start_line)
        for method in materialize_integration_methods(tmp_path, [], [_PATH], [module])
    }
    engine = load_engine(engine_name)
    assert engine is not None
    request = EngineRequest(repo_root=tmp_path, allowed_paths=[_PATH], timeout_seconds=600)
    with engine.open(request) as session:
        calls = session.calls()
    return sorted(
        (call.callee, call.dispatch_confidence)
        for call in calls
        if call.caller == caller and (call.callee_path, call.callee_line) in indexed
    )


_S10 = (
    "package p;\n"
    "import lombok.Data;\n"
    "import org.acme.ExternalNamed;\n"
    "interface Named { String name(); }\n"
    "class Written implements Named { public String name() { return \"\"; } }\n"
    "record Generated(String name) implements Named {}\n"
    "interface HasId { String getId(); }\n"
    "class WrittenId implements HasId { public String getId() { return \"\"; } }\n"
    "@Data class LombokId implements HasId { private String id; }\n"
    "interface Labeled { String label(); }\n"
    "class WrittenLabel implements Labeled { public String label() { return \"\"; } }\n"
    "class InheritedLabel extends ExternalNamed implements Labeled {}\n"
    "interface Shown { String toString(); }\n"
    "class WrittenShown implements Shown { public String toString() { return \"\"; } }\n"
    "class ObjectShown implements Shown {}\n"
    "interface Solo { String only(); }\n"
    "class SoloImpl implements Solo { public String only() { return \"\"; } }\n"
    "class Svc {\n"
    "  void run(Named n, HasId h, Labeled l, Shown s, Solo solo) {\n"
    "    n.name(); h.getId(); l.label(); s.toString(); solo.only();\n"
    "  }\n"
    "}\n"
)

_S10_LOMBOK_OVERRIDE = (
    "package p;\n"
    "import lombok.Data;\n"
    "class Base { public String getName() { return \"\"; } }\n"
    "@Data class Child extends Base { private String name; }\n"
    "class Svc { void run(Base base) { base.getName(); } }\n"
)


_TYPED_ENGINES = [
    pytest.param("codeql", marks=[
        pytest.mark.slow,
        pytest.mark.skipif(codeql_executable() is None, reason="Local CodeQL is required"),
    ], id="codeql"),
    pytest.param("joern", marks=[
        pytest.mark.slow,
        pytest.mark.skipif(
            shutil.which("joern") is None or declaration("joern").unavailable_reason() is not None,
            reason="Joern is not installed or its launcher prerequisites are missing",
        ),
    ], id="joern"),
]


def test_s9_an_override_with_an_unresolved_signature_keeps_the_base_body_possible(
    tmp_path: Path,
) -> None:
    calls = _calls(tmp_path, (
        "package p;\n"
        "import org.acme.*;\n"
        "class Base { void log(Object... values) {} void send(Request r) {} }\n"
        "class Loud extends Base { void log(Object... values) {} void send(Request r) {} }\n"
        "class Svc { void run(Base base, Request r) { base.log(1); base.send(r); } }\n"
    ))

    # The varargs and wildcard-typed overrides are possible implementors.
    assert calls == [
        ("p.Base.log", "possible"), ("p.Base.send", "possible"),
        ("p.Loud.log", "possible"), ("p.Loud.send", "possible"),
    ]


def test_s10_generated_or_inherited_implementors_keep_the_written_body_possible(
    tmp_path: Path,
) -> None:
    calls = _calls(tmp_path, _S10)

    # A record accessor, a Lombok getter, a body inherited from an external
    # class and ``Object.toString`` are implementors without a source body.
    assert ("p.Written.name", "possible") in calls
    assert ("p.WrittenId.getId", "possible") in calls
    assert ("p.WrittenLabel.label", "possible") in calls
    assert ("p.WrittenShown.toString", "possible") in calls
    # A complete implementor set keeps its unique body exact.
    assert ("p.SoloImpl.only", "exact") in calls


def test_s10_a_lombok_member_overriding_an_inherited_body_keeps_it_possible(tmp_path: Path) -> None:
    calls = _calls(tmp_path, _S10_LOMBOK_OVERRIDE)

    assert calls == [("p.Base.getName", "possible")]


@pytest.mark.parametrize("engine_name", _TYPED_ENGINES)
def test_s10_typed_engines_keep_generated_or_inherited_implementors_possible(
    tmp_path: Path, engine_name: str,
) -> None:
    """BL-32: CodeQL and Joern apply ADR-81 rule 1 as the ``source`` engine does."""
    calls = _engine_calls(tmp_path, engine_name, _S10)

    assert calls == [
        ("p.HasId.getId", "possible"), ("p.Labeled.label", "possible"),
        ("p.Named.name", "possible"), ("p.Shown.toString", "possible"),
        ("p.Solo.only", "possible"), ("p.SoloImpl.only", "exact"),
        ("p.Written.name", "possible"), ("p.WrittenId.getId", "possible"),
        ("p.WrittenLabel.label", "possible"), ("p.WrittenShown.toString", "possible"),
    ]


@pytest.mark.parametrize("engine_name", _TYPED_ENGINES)
def test_s10_typed_engines_keep_a_lombok_override_possible(tmp_path: Path, engine_name: str) -> None:
    """BL-32: a Lombok getter overriding a written body keeps it possible."""
    calls = _engine_calls(tmp_path, engine_name, _S10_LOMBOK_OVERRIDE)

    assert calls == [("p.Base.getName", "possible")]


def test_q8_a_feign_fallback_is_a_possible_target_beside_the_http_method(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "package p;\n"
        "import org.springframework.cloud.openfeign.FeignClient;\n"
        "import org.springframework.web.bind.annotation.GetMapping;\n"
        "@FeignClient(name = \"users\", fallback = UsersFallback.class)\n"
        "interface UsersClient { @GetMapping(\"/users\") String list(); }\n"
        "class UsersFallback implements UsersClient { public String list() { return \"\"; } }\n"
        "class Svc { private UsersClient users; void run() { users.list(); } }\n"
    ))

    # The interface method carries the HTTP call; the fallback is possible.
    assert calls == [("p.UsersClient.list", "possible"), ("p.UsersFallback.list", "possible")]
