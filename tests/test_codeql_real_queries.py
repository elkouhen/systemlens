"""Query contracts checked against the real CodeQL binary (slow).

Each database is built once per test module in a temporary directory.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from systemlens.discovery.java import parser
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.engines import codeql

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("codeql") is None, reason="Local CodeQL is required"),
]


def _database(root: Path, files: dict[str, str]) -> Path:
    db = root / "db"
    for rel, text in files.items():
        (root / "repo" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "repo" / rel).write_text(text, encoding="utf-8")
    subprocess.run(["codeql", "database", "create", str(db), "--language=java",
                    f"--source-root={root / 'repo'}", "--build-mode=none"], check=True, capture_output=True)
    return db


@pytest.fixture(scope="module")
def two_modules_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _database(tmp_path_factory.mktemp("ql"), {
        "orders/src/main/java/com/acme/Orders.java":
            "package com.acme;\npublic class Orders {\n  public void place() { new Pay().charge(); }\n}\n",
        "payments/src/main/java/com/acme/Pay.java":
            "package com.acme;\npublic class Pay {\n  public void charge() { }\n}\n",
    })


@pytest.fixture(scope="module")
def annotated_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _database(tmp_path_factory.mktemp("ql2"), {
        "src/main/java/com/acme/OrderRepository.java":
            "package com.acme;\npublic interface OrderRepository {\n  Order find(long id);\n"
            "  default Order require(long id) { return find(id); }\n"
            "  static OrderRepository empty() { return null; }\n}\n",
        "src/main/java/com/acme/Order.java": "package com.acme;\npublic class Order {}\n",
        "src/main/java/com/acme/OrderService.java":
            "package com.acme;\npublic class OrderService {\n  private OrderRepository repository;\n\n"
            "  @Deprecated\n  @SuppressWarnings(\"unused\")\n  public Order load(long id) {\n"
            "    OrderRepository r = OrderRepository.empty();\n    return repository.require(id);\n  }\n}\n",
    })


def test_module_scoped_method_query_returns_the_module_methods(two_modules_db: Path) -> None:
    unscoped = codeql.extract_codeql_methods(two_modules_db, executable="codeql")
    assert any(m.path.startswith("orders/") for m in unscoped)
    scoped = codeql.extract_codeql_methods(two_modules_db, executable="codeql", source_prefix="orders")
    assert [m.path for m in scoped] == [m.path for m in unscoped if m.path.startswith("orders/")]


def test_caller_scoped_calls_query_returns_the_module_calls(two_modules_db: Path) -> None:
    unscoped = codeql.extract_codeql_calls(two_modules_db, executable="codeql")
    assert unscoped and unscoped[0].caller_path.startswith("orders/")
    scoped = codeql.extract_codeql_calls(two_modules_db, executable="codeql", caller_prefix="orders")
    assert scoped == unscoped


def test_reachability_anchor_matches_tree_sitter_start_line_of_annotated_method(annotated_db: Path) -> None:
    source, root = parser.parse_java(str(annotated_db.parent / "repo"), "src/main/java/com/acme/OrderService.java")
    [declaration] = [n for n in parser.walk(root) if n.type == "method_declaration"]
    tree_sitter_start = declaration.start_point.row + 1  # the first annotation line
    assert tree_sitter_start == 5
    methods = [
        IntegrationMethod("in", "m", "com.acme.OrderService.load", "src/main/java/com/acme/OrderService.java",
                          tree_sitter_start, declaration.end_point.row + 1, ("in",), ()),
        IntegrationMethod("out", "m", "com.acme.OrderRepository.require",
                          "src/main/java/com/acme/OrderRepository.java", 4, 4, (), ("out",)),
    ]
    assert codeql.extract_codeql_reachability(annotated_db, methods, executable="codeql") != []


def test_one_call_is_not_reported_with_two_confidences(annotated_db: Path) -> None:
    calls = codeql.extract_codeql_calls(annotated_db, executable="codeql")
    keyed: dict[tuple, set[str]] = {}
    for call in calls:
        keyed.setdefault((call.caller, call.call_line, call.callee), set()).add(call.dispatch_confidence)
    conflicting = {k: v for k, v in keyed.items() if len(v) > 1}
    assert conflicting == {}, conflicting
