from pathlib import Path

import pytest

from systemlens.discovery.java import parser as java
from systemlens.scanner.java_strings import local_string


def _resolve(body: str) -> list[str | None]:
    source = body.encode()
    root = java.java_parser().parse(source).root_node
    return [local_string(source, args[0]) for node in java.walk(root)
            if node.type == "method_invocation"
            for _, name, args in [java.invocation_parts(node, source)]
            if name == "exchange"]


def test_literal_helper_uses_each_callers_lexical_scope() -> None:
    assert _resolve('''class Client {
      private String url(String service) { return "http://" + service; }
      void first() { String base = url("orders"); rest.exchange(base + "/orders"); }
      void second() { String base = url("stock"); rest.exchange(base + "/stock"); }
    }''') == ["http://orders/orders", "http://stock/stock"]


@pytest.mark.parametrize("helper", [
    'String url(String service) { return "http://" + service; }',
    'private String url(String service) { if (ready) return "http://" + service; return other; }',
    'private String url(String service) { return url(service); }',
    'private String url(String service) { return "http://" + service; } private String url(int n) { return "x"; }',
])
def test_unproven_helper_remains_unresolved(helper: str) -> None:
    assert _resolve('class Client {' + helper +
                    ' void run() { String base = url("orders"); rest.exchange(base); }}') == [None]


@pytest.mark.parametrize("body", [
    'String base = "http://orders"; void set(String value) { base = value; } void run() { rest.exchange(base); }',
    'String base = "http://orders"; void run(String base) { rest.exchange(base); }',
    'void run() { String base = "http://orders"; base = other; rest.exchange(base); }',
    'void run() { { String base = "http://orders"; } rest.exchange(base); }',
    'String base = "http://orders"; void run() { String base = dynamic(); rest.exchange(base); }',
    'void run() { rest.exchange(base); String base = "http://orders"; }',
    'private String url(String s) { return "http://" + s; } void run() { rest.exchange(other.url("orders")); }',
])
def test_mutation_shadowing_and_foreign_receivers_remain_unresolved(body: str) -> None:
    assert _resolve('class Client {' + body + '}') == [None]


def test_source_context_resolution_does_not_cache_stale_literals(tmp_path: Path) -> None:
    # Exercise two successive source snapshots in one interpreter.
    assert _resolve('class C { void run() { String base="http://a"; r.exchange(base); }}') == ["http://a"]
    assert _resolve('class C { void run() { String base="http://b"; r.exchange(base); }}') == ["http://b"]
