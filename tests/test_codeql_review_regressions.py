"""Regressions of the CodeQL findings of the 2026-11-04 call-graph review.

Each test builds a small CodeQL database with the real binary (slow) and
names the finding it protects, from the call-graph review plan in Git at
``4a12ad5`` (``docs/plans/call-graph-review-2026-11-04.md``).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from systemlens.engines import codeql
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.sqlite import Store

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("codeql") is None, reason="Local CodeQL is required"),
]

_POM = (
    "<project><modelVersion>4.0.0</modelVersion><groupId>com.acme</groupId>"
    "<artifactId>shop</artifactId><version>1.0</version></project>"
)


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _create_database(source_root: Path, database: Path) -> Path:
    subprocess.run(
        ["codeql", "database", "create", str(database), "--language=java",
         f"--source-root={source_root}", "--build-mode=none"],
        check=True, capture_output=True,
    )
    return database


_API = """package com.acme;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class Api {
    private Pub pub;

    @GetMapping("/orders")
    public void orders() {
        pub.send();
    }
}
"""

_PUB = """package com.acme;

import org.springframework.kafka.core.KafkaTemplate;

public class Pub {
    private KafkaTemplate<String, String> kafkaTemplate;

    public void send() {
        kafkaTemplate.send("orders", "x");
    }
}
"""


def _shop(root: Path) -> Path:
    _write(root, {
        "pom.xml": _POM,
        "src/main/java/com/acme/Api.java": _API,
        "src/main/java/com/acme/Pub.java": _PUB,
    })
    return root


def test_q1_an_index_refuses_a_database_built_from_older_sources(tmp_path: Path) -> None:
    repo = _shop(tmp_path / "repo")
    database = _create_database(repo, tmp_path / "db")
    # A later revision inserts a method before the call: the database's call
    # line now falls into another method.
    api = repo / "src/main/java/com/acme/Api.java"
    api.write_text(api.read_text().replace(
        "    private Pub pub;\n",
        "    private Pub pub;\n\n    public void added() {\n    }\n",
    ))

    with Store(repo) as store:
        with pytest.raises(RuntimeError, match="stale"):
            index_repo(repo, Config(), store, full=True,
                       call_graph_engine=codeql.CodeQLEngine(database=database))


def test_q5_an_index_refuses_a_database_with_another_source_root(tmp_path: Path) -> None:
    repo = _shop(tmp_path / "repo")
    database = _create_database(repo / "src/main/java", tmp_path / "db")

    with Store(repo) as store:
        with pytest.raises(RuntimeError, match="another source root"):
            index_repo(repo, Config(), store, full=True,
                       call_graph_engine=codeql.CodeQLEngine(database=database))


def test_q1_a_database_of_the_indexed_sources_is_used(tmp_path: Path) -> None:
    repo = _shop(tmp_path / "repo")
    database = _create_database(repo, tmp_path / "db")

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True,
                   call_graph_engine=codeql.CodeQLEngine(database=database))
        flows = store.all_code_flows()
        assert store.get_meta("codeql_call_graph_status") == "complete"
        assert "|engine-input=" in (store.get_meta("code_flow_signature") or "")

    assert [[step.kind for step in flow.steps] for flow in flows] == [
        ["http_entry", "method_call", "message_publish"],
    ]


def test_q9_overloads_declared_on_one_line_keep_their_edges(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, {
        "pom.xml": _POM,
        "src/main/java/com/acme/Api.java": _API.replace(
            "        pub.send();\n", "        pub.a();\n        pub.a(1);\n",
        ),
        "src/main/java/com/acme/Pub.java": (
            "package com.acme;\n\n"
            "import org.springframework.kafka.core.KafkaTemplate;\n\n"
            "public class Pub {\n"
            "    private KafkaTemplate<String, String> kafkaTemplate;\n"
            "    public void a() { kafkaTemplate.send(\"a\", \"x\"); }"
            " public void a(int i) { kafkaTemplate.send(\"a-int\", \"x\"); }\n"
            "}\n"
        ),
    })
    database = _create_database(repo, tmp_path / "db")

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True,
                   call_graph_engine=codeql.CodeQLEngine(database=database))
        methods = {method.id: method for method in store.all_integration_methods()}
        edges = [
            (methods[edge.callee_id].parameter_types, edge.line)
            for edge in store.all_call_edges()
            if edge.callee_id in methods
            and methods[edge.callee_id].qualified_method == "com.acme.Pub.a"
        ]
        published = sorted(
            flow.steps[-1].start_line for flow in store.all_code_flows()
            if flow.steps and flow.steps[-1].kind == "message_publish"
        )

    assert sorted(edges) == [((), 12), (("int",), 13)]
    # One flow per overload, each to its own send site on line 7.
    assert published == [7, 7]


def _calls(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, int, str, int, str]]:
    _write(tmp_path / "repo", files)
    database = _create_database(tmp_path / "repo", tmp_path / "db")
    return {
        (call.caller, call.call_line, call.callee, call.callee_line, call.dispatch_confidence)
        for call in codeql.extract_codeql_calls(database, executable="codeql")
    }


def test_q2_an_untyped_argument_reaches_every_overload_as_possible(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "src/main/java/com/acme/Invoice.java": "package com.acme;\npublic class Invoice {}\n",
        "src/main/java/com/acme/Order.java": "package com.acme;\npublic class Order {}\n",
        "src/main/java/com/acme/Pub.java": (
            "package com.acme;\n"
            "public class Pub {\n"
            "    public void emit(Invoice i) { }\n"
            "    public void emit(Order o) { }\n"
            "    public void emit(Order o, int n) { }\n"
            "}\n"
        ),
        "src/main/java/com/acme/Api.java": (
            "package com.acme;\n"
            "import org.modelmapper.ModelMapper;\n"
            "public class Api {\n"
            "    private Pub pub;\n"
            "    private ModelMapper mapper;\n"
            "    public void unknown(Object dto) {\n"
            "        pub.emit(mapper.convert(dto));\n"
            "    }\n"
            "    public void known(Order order) {\n"
            "        pub.emit(order);\n"
            "    }\n"
            "}\n"
        ),
    })

    assert {call for call in calls if call[0] == "com.acme.Api.unknown"} == {
        ("com.acme.Api.unknown", 7, "com.acme.Pub.emit", 3, "possible"),
        ("com.acme.Api.unknown", 7, "com.acme.Pub.emit", 4, "possible"),
    }
    assert {call for call in calls if call[0] == "com.acme.Api.known"} == {
        ("com.acme.Api.known", 10, "com.acme.Pub.emit", 4, "exact"),
    }


def test_q7_a_generic_receiver_keeps_its_declared_method_row(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "src/main/java/com/acme/Gateway.java": (
            "package com.acme;\npublic interface Gateway<T> {\n    void send(T t);\n}\n"
        ),
        "src/main/java/com/acme/Two.java": (
            "package com.acme;\npublic interface Two<T> {\n    void on(T t);\n}\n"
        ),
        "src/main/java/com/acme/TwoA.java": (
            "package com.acme;\npublic class TwoA implements Two<String> {\n"
            "    public void on(String t) { }\n}\n"
        ),
        "src/main/java/com/acme/TwoB.java": (
            "package com.acme;\npublic class TwoB implements Two<String> {\n"
            "    public void on(String t) { }\n}\n"
        ),
        "src/main/java/com/acme/Api.java": (
            "package com.acme;\n"
            "public class Api {\n"
            "    private Gateway<String> gateway;\n"
            "    private Two<String> two;\n"
            "    public void generic() {\n"
            "        gateway.send(\"x\");\n"
            "    }\n"
            "    public void two() {\n"
            "        two.on(\"x\");\n"
            "    }\n"
            "}\n"
        ),
    })

    # The declared-method row of a call without a visible implementation.
    assert ("com.acme.Api.generic", 6, "com.acme.Gateway.send", 3, "possible") in calls
    # The interface witness beside the concrete candidates.
    assert ("com.acme.Api.two", 9, "com.acme.Two.on", 3, "possible") in calls


_PORT = "package com.acme;\npublic interface Port {\n    void send();\n}\n"
_ADAPTER = (
    "package com.acme;\npublic class Adapter implements Port {\n"
    "    public void send() { }\n}\n"
)
_CALLER = (
    "package com.acme;\npublic class Api {\n    private Port port;\n"
    "    public void run() {\n        port.send();\n    }\n}\n"
)


def test_q6_a_test_implementation_never_lowers_a_production_exact_edge(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "src/main/java/com/acme/Port.java": _PORT,
        "src/main/java/com/acme/Adapter.java": _ADAPTER,
        "src/main/java/com/acme/Api.java": _CALLER,
        "src/test/java/com/acme/FakePort.java": (
            "package com.acme;\npublic class FakePort implements Port {\n"
            "    public void send() { }\n}\n"
        ),
    })

    # ADR-81 rule 2: the user-built database holds src/test, which is no
    # part of the world.
    assert {call for call in calls if call[0] == "com.acme.Api.run"} == {
        ("com.acme.Api.run", 5, "com.acme.Adapter.send", 3, "exact"),
        ("com.acme.Api.run", 5, "com.acme.Port.send", 3, "possible"),
    }


def test_q8_a_feign_fallback_is_never_the_exact_target(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "src/main/java/com/acme/UsersClient.java": (
            "package com.acme;\n"
            "import org.springframework.cloud.openfeign.FeignClient;\n"
            "@FeignClient(name = \"users\", fallback = UsersFallback.class)\n"
            "public interface UsersClient {\n    String list();\n}\n"
        ),
        "src/main/java/com/acme/UsersFallback.java": (
            "package com.acme;\npublic class UsersFallback implements UsersClient {\n"
            "    public String list() { return \"\"; }\n}\n"
        ),
        "src/main/java/com/acme/Api.java": (
            "package com.acme;\npublic class Api {\n    private UsersClient users;\n"
            "    public void run() {\n        users.list();\n    }\n}\n"
        ),
    })

    # ADR-81 rule 3: the interface method carries the HTTP call.
    assert {call for call in calls if call[0] == "com.acme.Api.run"} == {
        ("com.acme.Api.run", 5, "com.acme.UsersClient.list", 5, "possible"),
        ("com.acme.Api.run", 5, "com.acme.UsersFallback.list", 3, "possible"),
    }


def test_q11_object_methods_on_an_unknown_runtime_type_have_no_edge(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "src/main/java/com/acme/Money.java": (
            "package com.acme;\npublic class Money {\n"
            "    public boolean equals(Object o) { return true; }\n"
            "    public int hashCode() { return 1; }\n}\n"
        ),
        "src/main/java/com/acme/Trip.java": (
            "package com.acme;\npublic class Trip {\n"
            "    public boolean equals(Object o) { return true; }\n"
            "    public int hashCode() { return 1; }\n}\n"
        ),
        "src/main/java/com/acme/Api.java": (
            "package com.acme;\npublic class Api {\n"
            "    public boolean same(Object a, Object b) {\n"
            "        return a.equals(b) && a.hashCode() == b.hashCode();\n    }\n"
            "    public <T> int hash(T value) {\n        return value.hashCode();\n    }\n"
            "    public int money(Money money) {\n        return money.hashCode();\n    }\n"
            "}\n"
        ),
    })

    # ADR-81 rule 4: no fan-out to every override; a typed receiver keeps
    # its edge.
    assert {call for call in calls if call[0].startswith("com.acme.Api.")} == {
        ("com.acme.Api.money", 10, "com.acme.Money.hashCode", 4, "exact"),
    }
