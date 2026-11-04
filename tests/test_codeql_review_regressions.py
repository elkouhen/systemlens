"""Regressions of the CodeQL findings of the 2026-11-04 call-graph review.

Each test builds a small CodeQL database with the real binary (slow) and
names the finding it protects, from ``docs/plans/call-graph-review-2026-11-04.md``.
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
