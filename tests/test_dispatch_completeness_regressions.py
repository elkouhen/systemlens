"""Regressions of ADR-81 for the ``source`` engine (backlog item BL-01).

A call is ``exact`` only when every implementor of the receiver has a
known body and exactly one applies. Each test names its finding in the
2026-11-04 call-graph review (``docs/plans/call-graph-review-2026-11-04.md``
in Git at ``4a12ad5``).
"""
from pathlib import Path

from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing.java_symbols import JavaSymbols


def _calls(tmp_path: Path, source: str, caller: str = "p.Svc.run") -> list[tuple[str, str]]:
    path = "app/src/main/java/p/Svc.java"
    (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / path).write_text(source, encoding="utf-8")
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    methods = materialize_integration_methods(tmp_path, [], [path], [module])
    symbols = JavaSymbols(tmp_path, methods, [path])
    return sorted(
        (call.callee, call.dispatch_confidence)
        for call in symbols.fallback_calls(set(), closed_world=True)
        if call.caller == caller
    )


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
    calls = _calls(tmp_path, (
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
    ))

    # A record accessor, a Lombok getter, a body inherited from an external
    # class and ``Object.toString`` are implementors without a source body.
    assert ("p.Written.name", "possible") in calls
    assert ("p.WrittenId.getId", "possible") in calls
    assert ("p.WrittenLabel.label", "possible") in calls
    assert ("p.WrittenShown.toString", "possible") in calls
    # A complete implementor set keeps its unique body exact.
    assert ("p.SoloImpl.only", "exact") in calls


def test_s10_a_lombok_member_overriding_an_inherited_body_keeps_it_possible(tmp_path: Path) -> None:
    calls = _calls(tmp_path, (
        "package p;\n"
        "import lombok.Data;\n"
        "class Base { public String getName() { return \"\"; } }\n"
        "@Data class Child extends Base { private String name; }\n"
        "class Svc { void run(Base base) { base.getName(); } }\n"
    ))

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
