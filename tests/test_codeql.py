from pathlib import Path
from subprocess import CompletedProcess
import os
import subprocess
import sys
import time

import pytest

from systemlens.domain.code_flows import IntegrationMethod
from systemlens.engines import codeql
from systemlens.indexing import source_generation


def test_query_scopes_source_calls_and_folds_dispatch_resolution() -> None:
    assert "class SourceMethodCall extends MethodCall" in codeql._QUERY
    assert "this.getEnclosingCallable().fromSource()" in codeql._QUERY
    assert "predicate exactTarget(MethodCall call, Method target)" in codeql._QUERY
    assert "predicate resolvedTarget(MethodCall call, Method target, string confidence)" in codeql._QUERY
    # ``exactVirtualMethod`` without test bodies (ADR-81 rule 2).
    assert "forall(Callable other | other = viableCallable(call) and other != target | testSource(other))" in (
        codeql._QUERY
    )
    assert "not exists(Method exact | exactTarget(call, exact))" in codeql._QUERY
    assert "target.fromSource()" in codeql._QUERY


def test_methods_query_can_be_scoped_to_a_module_source_prefix() -> None:
    query = codeql._methods_query("orders-service")

    assert "method.fromSource()" in query
    assert 'path.regexpMatch("^orders\\\\-service/.*")' in query


def test_reachability_query_walks_from_outputs_to_input_methods() -> None:
    methods = [
        IntegrationMethod(
            id="input", module="orders", qualified_method="Orders.in", path="Orders.java",
            start_line=10, end_line=12, input_endpoint_ids=("in",), output_endpoint_ids=(),
        ),
        IntegrationMethod(
            id="output", module="orders", qualified_method="Orders.out", path="Orders.java",
            start_line=20, end_line=22, input_endpoint_ids=(), output_endpoint_ids=("out",),
        ),
    ]

    query = codeql._reachability_query(methods)

    assert "predicate exactCallerReachable(Callable callee, Callable caller)" in query
    assert "predicate possibleCallerReachable(Callable callee, Callable caller)" in query
    assert "exactCallerReachable(outputMethod, inputMethod)" in query
    assert "outputAnchor(callee) and exactEdge(caller, callee)" in query
    assert "exactCallerReachable(callee, previous) and exactEdge(caller, previous)" in query
    assert "depth" not in query
    for column in ("source", "source_path", "source_line", "target", "target_path", "target_line"):
        assert f" as {column}" in query


@pytest.mark.parametrize("script", ["import time; time.sleep(30)",
                                  "import time; print('working', flush=True); time.sleep(30)"])
def test_progress_timeout_covers_reading_stdout(script: str) -> None:
    output: list[str] = []
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        codeql._run_with_progress([sys.executable, "-c", script], timeout=1, progress=output.append)
    assert time.monotonic() - started < 5
    if "print" in script:
        assert output == ["working"]


def test_progress_streams_and_returns_output() -> None:
    output: list[str] = []
    result = codeql._run_with_progress(
        [sys.executable, "-c", "print('first'); print('second')"], timeout=5, progress=output.append,
    )
    assert result.returncode == 0
    assert result.stdout == "first\nsecond\n"
    assert output == ["first", "second"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
def test_progress_deadline_kills_descendant_holding_stdout_open() -> None:
    script = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        codeql._run_with_progress([sys.executable, "-c", script], timeout=1, progress=lambda _line: None)
    assert time.monotonic() - started < 5


def test_reachability_decodes_named_columns(tmp_path: Path, monkeypatch) -> None:
    methods = [IntegrationMethod("in", "m", "C.in", "C.java", 1, 1, ("in",), ()),
               IntegrationMethod("out", "m", "C.out", "C.java", 2, 2, (), ("out",))]

    def run(command, **kwargs):
        if command[1:3] == ["bqrs", "decode"]:
            output = Path(next(arg.removeprefix("--output=") for arg in command if arg.startswith("--output=")))
            output.write_text("source,source_path,source_line,target,target_path,target_line,confidence\n"
                              "C.in,C.java,1,C.out,C.java,2,medium\n")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.database, "_run_with_progress", run)
    assert codeql.extract_codeql_reachability(tmp_path, methods, executable="codeql") == [
        codeql.MethodReachability("C.in", "C.java", 1, "C.out", "C.java", 2, "medium")
    ]


def test_source_only_root_keeps_generated_sources_but_excludes_build_outputs(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project />", encoding="utf-8")
    (tmp_path / "service" / "src" / "Main.java").parent.mkdir(parents=True)
    (tmp_path / "service" / "src" / "Main.java").write_text("class Main {}", encoding="utf-8")
    (tmp_path / "service" / "target").mkdir()
    (tmp_path / "service" / "target" / "Generated.java").write_text("class Generated {}", encoding="utf-8")
    (tmp_path / "service" / "target" / "generated-sources" / "asyncapi").mkdir(parents=True)
    (tmp_path / "service" / "target" / "generated-sources" / "asyncapi" / "OrderPlaced.java").write_text(
        "class OrderPlaced {}", encoding="utf-8"
    )
    destination = tmp_path / "source"

    assert codeql._prepare_source_only_root(tmp_path, destination) == 2
    assert (destination / "service" / "src" / "Main.java").exists()
    assert not (destination / "pom.xml").exists()
    assert not (destination / "service" / "target" / "Generated.java").exists()
    assert (destination / "service" / "target" / "generated-sources" / "asyncapi" / "OrderPlaced.java").exists()


def test_source_only_root_can_include_all_target_java_sources(tmp_path: Path) -> None:
    generated = tmp_path / "service/target/custom/GeneratedMapper.java"
    generated.parent.mkdir(parents=True)
    generated.write_text("class GeneratedMapper {}", encoding="utf-8")

    destination = tmp_path / "source"

    assert codeql._prepare_source_only_root(
        tmp_path, destination, include_target_java_sources=True
    ) == 1
    assert (destination / "service/target/custom/GeneratedMapper.java").exists()


def test_source_only_root_respects_indexed_source_perimeter(tmp_path: Path) -> None:
    allowed = tmp_path / "src/main/java/App.java"
    excluded = tmp_path / "src/test/java/AppTest.java"
    allowed.parent.mkdir(parents=True)
    excluded.parent.mkdir(parents=True)
    allowed.write_text("class App {}", encoding="utf-8")
    excluded.write_text("class AppTest {}", encoding="utf-8")
    destination = tmp_path / "source"

    assert codeql._prepare_source_only_root(
        tmp_path, destination, allowed_paths=[allowed.relative_to(tmp_path).as_posix()]
    ) == 1
    assert (destination / "src/main/java/App.java").exists()
    assert not (destination / "src/test/java/AppTest.java").exists()


def test_source_only_root_keeps_source_packages_named_build(tmp_path: Path) -> None:
    source = tmp_path / "service/src/main/java/com/example/build/Order.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Order {}", encoding="utf-8")
    destination = tmp_path / "source"

    assert codeql._prepare_source_only_root(tmp_path, destination) == 1
    assert (destination / "service/src/main/java/com/example/build/Order.java").exists()


def test_generate_sources_runs_only_the_maven_generation_phase(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pom.xml").write_text("<project />", encoding="utf-8")
    observed: list[tuple[list[str], Path | None]] = []

    def run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        observed.append((command, kwargs.get("cwd")))
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(source_generation, "run_with_progress", run)
    source_generation.generate_sources(tmp_path, timeout=42, progress=None)

    assert observed == [(["mvn", "-B", "-ntp", "-o", "generate-sources"], tmp_path)]


def test_generate_sources_runs_independent_nested_maven_projects(
    tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "services/orders"
    second = tmp_path / "services/payments"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "pom.xml").write_text("<project />", encoding="utf-8")
    (second / "pom.xml").write_text("<project />", encoding="utf-8")
    observed: list[Path | None] = []

    def run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        assert command == ["mvn", "-B", "-ntp", "-o", "generate-sources"]
        observed.append(kwargs.get("cwd"))
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(source_generation, "run_with_progress", run)
    source_generation.generate_sources(tmp_path, timeout=42, progress=None)

    assert observed == [first, second]


def test_automatic_codeql_database_is_source_only_and_temporary(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[tuple[list[str], int | None]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        commands.append((command, _kwargs.get("timeout")))
        Path(command[3]).mkdir()
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.database, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(codeql.database, "_run_with_progress", run)

    with codeql.automatic_codeql_database(
        tmp_path, timeout_seconds=42, threads=4, ram_mb=4096
    ) as database:
        assert database is not None
        assert database.is_dir()
        database_path = database

    assert not database_path.exists()
    assert commands == [([
        "codeql", "database", "create", str(database_path), "--language=java",
        f"--source-root={database_path.parent / 'source'}", "--build-mode=none", "--threads=4", "--ram=4096",
    ], 42)]


def test_automatic_codeql_database_is_reused_while_the_sources_are_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    """Q12 of the call-graph review: the automatic database was rebuilt on
    every index (97 s against 20 s prebuilt on train-ticket)."""
    source = tmp_path / "src/main/java/A.java"
    source.parent.mkdir(parents=True)
    source.write_text("class A {}", encoding="utf-8")
    created: list[str] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        created.append(command[3])
        Path(command[3]).mkdir()
        (Path(command[3]) / "codeql-database.yml").write_text("finalised: true\n")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.database, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(codeql.database, "_run_with_progress", run)

    databases = []
    for text in ("class A {}", "class A {}", "class A { void m() {} }"):
        source.write_text(text, encoding="utf-8")
        with codeql.automatic_codeql_database(tmp_path) as database:
            assert database is not None and (database / "codeql-database.yml").is_file()
            databases.append(database)

    assert len(created) == 2, "unchanged sources reuse the database; a change rebuilds it"
    assert databases[0] == databases[1] == databases[2] == tmp_path / ".systemlens/codeql/java-database"


def test_automatic_codeql_database_includes_generated_java_sources(
    tmp_path: Path, monkeypatch
) -> None:
    generated = tmp_path / "service/target/generated-sources/asyncapi/OrderPlaced.java"
    generated.parent.mkdir(parents=True)
    generated.write_text("class OrderPlaced {}", encoding="utf-8")
    source_roots: list[Path] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        source_root = Path(next(
            argument.removeprefix("--source-root=")
            for argument in command
            if argument.startswith("--source-root=")
        ))
        source_roots.append(source_root)
        Path(command[3]).mkdir()
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.database, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(codeql.database, "_run_with_progress", run)

    with codeql.automatic_codeql_database(tmp_path) as database:
        assert database is not None
        assert len(source_roots) == 1
        assert (
            source_roots[0]
            / "service/target/generated-sources/asyncapi/OrderPlaced.java"
        ).is_file()



def test_automatic_codeql_database_skips_when_codeql_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(codeql.database, "codeql_executable", lambda: None)

    with codeql.automatic_codeql_database(tmp_path) as database:
        assert database is None


def test_extract_codeql_calls_uses_pinned_local_pack_without_installing(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "database"
    database.mkdir()
    commands: list[tuple[list[str], int | None]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        commands.append((command, _kwargs.get("timeout")))
        if command[1:3] == ["bqrs", "decode"]:
            output = next(part.removeprefix("--output=") for part in command if part.startswith("--output="))
            Path(output).write_text(
                "caller,caller_path,caller_line,callee,callee_path,callee_line,call_line,dispatch_confidence\n"
                "com.example.A.run,A.java,1,com.example.B.send,B.java,2,3,exact\n",
                encoding="utf-8",
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.database, "_run_with_progress", run)

    calls = codeql.extract_codeql_calls(
        database, executable="custom-codeql", timeout_seconds=42, threads=4, ram_mb=4096
    )

    assert calls == [codeql.MethodCall(
        "com.example.A.run", "A.java", 1, "com.example.B.send", "B.java", 2, 3,
    )]
    assert all("pack" not in command[1:3] for command, _timeout in commands)
    assert all(command[0] == "custom-codeql" for command, _timeout in commands)
    assert all(timeout == 42 for _command, timeout in commands)
    assert "--threads=4" in commands[0][0]
    assert "--ram=4096" in commands[0][0]


def test_extract_codeql_methods_reads_source_nodes_directly(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "database"
    database.mkdir()

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if command[1:3] == ["bqrs", "decode"]:
            output = next(part.removeprefix("--output=") for part in command if part.startswith("--output="))
            Path(output).write_text(
                "qualified_method,path,start_line\n"
                "com.example.Service.reserve,Service.java,7\n",
                encoding="utf-8",
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.database, "_run_with_progress", run)

    methods = codeql.extract_codeql_methods(
        database, executable="custom-codeql", timeout_seconds=42, threads=4,
    )

    assert methods == [codeql.SourceMethod(
        "com.example.Service.reserve", "Service.java", 7,
    )]


def test_extract_codeql_kafka_message_types_reads_strategy1_payload_type(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "database"
    database.mkdir()

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if command[1:3] == ["bqrs", "decode"]:
            output = next(part.removeprefix("--output=") for part in command if part.startswith("--output="))
            Path(output).write_text(
                "path,line,message_type\nPublisher.java,7,OrderCreated\n",
                encoding="utf-8",
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.database, "_run_with_progress", run)

    types = codeql.extract_codeql_kafka_message_types(
        database, executable="custom-codeql", timeout_seconds=42, threads=4, ram_mb=4096
    )

    assert types == [codeql.PayloadTypeEvidence("Publisher.java", 7, "OrderCreated")]


def test_extract_codeql_calls_prefixes_module_relative_evidence_paths(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "database"
    database.mkdir()

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if command[1:3] == ["bqrs", "decode"]:
            output = next(part.removeprefix("--output=") for part in command if part.startswith("--output="))
            Path(output).write_text(
                "caller,caller_path,caller_line,callee,callee_path,callee_line,call_line,dispatch_confidence\n"
                "com.example.A.run,src/main/java/A.java,1,com.example.B.send,src/main/java/B.java,2,3,exact\n",
                encoding="utf-8",
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.database, "_run_with_progress", run)

    calls = codeql.extract_codeql_calls(
        database, executable="custom-codeql", path_prefix="module-a"
    )

    assert calls[0].caller_path == "module-a/src/main/java/A.java"
    assert calls[0].callee_path == "module-a/src/main/java/B.java"


def test_extract_codeql_calls_scopes_global_database_by_caller_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "database"
    database.mkdir()
    queries: list[str] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if command[1:3] == ["query", "run"]:
            query_path = next(part for part in command if part.endswith("calls.ql"))
            queries.append(Path(query_path).read_text(encoding="utf-8"))
        if command[1:3] == ["bqrs", "decode"]:
            output = next(part.removeprefix("--output=") for part in command if part.startswith("--output="))
            Path(output).write_text(
                "caller,caller_path,caller_line,callee,callee_path,callee_line,call_line,dispatch_confidence\n",
                encoding="utf-8",
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.database, "_run_with_progress", run)

    codeql.extract_codeql_calls(database, executable="custom-codeql", caller_prefix="payments")

    assert 'path = enclosing.getFile().getRelativePath() and path.regexpMatch("^payments/.*")' in queries[0]


def test_source_only_root_keeps_source_packages_named_target_or_out(tmp_path: Path) -> None:
    kept = [
        tmp_path / "app/src/main/java/com/acme/target/Foo.java",
        tmp_path / "app/src/main/java/com/acme/out/Bar.java",
    ]
    dropped = [
        tmp_path / "app/target/classes/Foo.java",
        tmp_path / "app/out/production/Bar.java",
    ]
    for path in [*kept, *dropped]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("class X {}", encoding="utf-8")
    destination = tmp_path / "source"

    assert codeql._prepare_source_only_root(tmp_path, destination) == 2
    for path in kept:
        assert (destination / path.relative_to(tmp_path)).exists()
    for path in dropped:
        assert not (destination / path.relative_to(tmp_path)).exists()


def test_a_call_query_deadline_reports_no_recovered_calls(
    tmp_path: Path, monkeypatch
) -> None:
    """Q4 of the call-graph review: CodeQL writes its BQRS result only when
    the query completes, so a deadline decodes nothing and recovers no call."""
    database = tmp_path / "database"
    database.mkdir()
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        commands.append(command)
        if command[1:3] == ["query", "run"]:
            raise subprocess.TimeoutExpired(command, 1)
        raise AssertionError("a timed-out query has no result to decode")

    monkeypatch.setattr(codeql.database, "_run_with_progress", run)

    with pytest.raises(codeql.CodeQLTimeout) as raised:
        codeql.engine._deadline_bound(
            codeql.extract_codeql_calls, database, executable="codeql", timeout_seconds=1,
        )

    assert raised.value.calls == []
    assert [command[1:3] for command in commands] == [["query", "run"]]


def _supplied_database(root: Path, prefix: str, archived: dict[str, str]) -> Path:
    """A CodeQL database shell: its metadata and its source archive."""
    import zipfile

    database = root / "db"
    database.mkdir(parents=True)
    (database / "codeql-database.yml").write_text(
        f"---\nsourceLocationPrefix: {prefix}\nbuildMode: none\nfinalised: true\n",
        encoding="utf-8",
    )
    with zipfile.ZipFile(database / "src.zip", "w") as archive:
        for name, text in archived.items():
            archive.writestr(name, text)
    return database


def test_a_database_built_from_older_sources_is_refused(tmp_path: Path) -> None:
    """Q1 of the call-graph review: old call lines would land in other
    methods as exact edges, so a stale database is refused."""
    repo = tmp_path / "repo"
    (repo / "src/main/java/a").mkdir(parents=True)
    (repo / "src/main/java/a/A.java").write_text("class A { void added() {} void m() {} }\n")
    (repo / "src/main/java/a/package-info.java").write_text("package a;\n")
    database = _supplied_database(tmp_path, "/work/repo", {
        "work/repo/src/main/java/a/A.java": "class A { void m() {} }\n",
    })

    with pytest.raises(codeql.CodeQLError, match="stale: 1 indexed Java file"):
        codeql.database.verify_database_sources(
            database, repo, ["src/main/java/a/A.java", "src/main/java/a/package-info.java"],
        )


def test_a_database_matching_the_sources_is_accepted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src/main/java/a").mkdir(parents=True)
    (repo / "src/main/java/a/A.java").write_text("class A { void m() {} }\n")
    (repo / "src/main/java/a/package-info.java").write_text("package a;\n")
    database = _supplied_database(tmp_path, "C:\\work\\repo", {
        "C_/work/repo/src/main/java/a/A.java": "class A { void m() {} }\n",
    })

    # CodeQL does not archive package-info.java: an absent file is no mismatch.
    assert codeql.database.verify_database_sources(
        database, repo,
        ["src/main/java/a/A.java", "src/main/java/a/package-info.java", "pom.xml"],
    ) == 1


def test_a_database_built_from_another_source_root_is_refused(tmp_path: Path) -> None:
    """Q5 of the call-graph review: no row would join, yet the status said
    complete and the symbol-pass edges looked like CodeQL's."""
    repo = tmp_path / "repo"
    (repo / "src/main/java/a").mkdir(parents=True)
    (repo / "src/main/java/a/A.java").write_text("class A { void m() {} }\n")
    database = _supplied_database(tmp_path, "/work/repo/src/main/java", {
        "work/repo/src/main/java/a/A.java": "class A { void m() {} }\n",
    })

    with pytest.raises(codeql.CodeQLError, match="another source root"):
        codeql.database.verify_database_sources(database, repo, ["src/main/java/a/A.java"])


def test_calls_that_join_no_indexed_file_are_refused(tmp_path: Path, monkeypatch) -> None:
    """Q5: the call rows themselves must name indexed files."""
    from systemlens.callgraph.engine import EngineRequest

    monkeypatch.setattr(codeql.database, "verify_database_sources", lambda *_args: 1)
    monkeypatch.setattr(codeql.extraction, "extract_codeql_calls", lambda *_a, **_k: [
        codeql.MethodCall("a.A.m", "a/A.java", 1, "a.B.n", "a/B.java", 1, 1),
    ])
    engine = codeql.CodeQLEngine(database=tmp_path / "db")
    request = EngineRequest(
        repo_root=tmp_path, allowed_paths=["src/main/java/a/A.java"], timeout_seconds=60,
    )

    with engine.open(request) as session:
        with pytest.raises(codeql.CodeQLError, match="another source root"):
            session.calls()


def test_overloads_declared_on_one_line_keep_their_codeql_edge() -> None:
    """Q9 of the call-graph review: two same-name overloads on one line
    shared the join key (name, path, line) and the edge was dropped."""
    from systemlens.indexing.code_flows import _build_call_graph

    out = "src/main/java/com/acme/Out.java"
    api = "src/main/java/com/acme/Api.java"
    methods = [
        IntegrationMethod("api", "m", "com.acme.Api.sameline", api, 13, 16, ("in",), (),
                          parameter_types=()),
        IntegrationMethod("a0", "m", "com.acme.Out.a", out, 7, 7, (), ("out-a",),
                          parameter_types=()),
        IntegrationMethod("a1", "m", "com.acme.Out.a", out, 7, 7, (), ("out-int",),
                          parameter_types=("int",)),
        IntegrationMethod("g0", "m", "com.acme.Out.g", out, 8, 8, (), (),
                          parameter_types=("java.util.List<String>", "String...")),
        IntegrationMethod("g1", "m", "com.acme.Out.g", out, 8, 8, (), (),
                          parameter_types=("Map<K, V>",)),
    ]
    calls = [
        codeql.MethodCall("com.acme.Api.sameline", api, 14, "com.acme.Out.a", out, 7, 15,
                          caller_parameter_types=(), callee_parameter_types=()),
        codeql.MethodCall("com.acme.Api.sameline", api, 14, "com.acme.Out.a", out, 7, 16,
                          caller_parameter_types=(), callee_parameter_types=("int",)),
        codeql.MethodCall("com.acme.Api.sameline", api, 14, "com.acme.Out.g", out, 8, 16,
                          callee_parameter_types=("List<String>", "String[]")),
    ]

    graph = _build_call_graph(methods, calls, repo_root=None, source_paths=[api, out],
                              progress=None)

    assert sorted((edge.callee_id, edge.line) for edge in graph.edges()) == [
        ("a0", 15), ("a1", 16), ("g0", 16),
    ]


def test_the_supplied_database_identity_enters_the_flow_signature(tmp_path: Path) -> None:
    """Q1: a recreated database recomputes the flows of unchanged sources."""
    first = _supplied_database(tmp_path / "one", "/work/repo", {})
    second = _supplied_database(tmp_path / "two", "/work/other", {})

    one = codeql.CodeQLEngine(database=first).input_fingerprint()
    two = codeql.CodeQLEngine(database=second).input_fingerprint()

    assert one and two and one != two
    assert str(tmp_path) not in one


def test_the_codeql_cli_enters_the_flow_signature_of_the_automatic_database(
    tmp_path: Path, monkeypatch,
) -> None:
    """Review A6: an upgraded CodeQL recomputed nothing for unchanged sources.

    ADR-84 keys the cached database on the executable; the flow signature
    must follow it, without naming a local path.
    """
    from systemlens.engines.codeql import database as codeql_database

    executable = tmp_path / "codeql"
    executable.write_text("2.27.3", encoding="utf-8")
    monkeypatch.setattr(codeql_database, "codeql_executable", lambda: str(executable))
    before = codeql.CodeQLEngine().input_fingerprint()
    executable.write_text("2.28.0 upgraded", encoding="utf-8")
    after = codeql.CodeQLEngine().input_fingerprint()
    monkeypatch.setattr(codeql_database, "codeql_executable", lambda: None)

    assert before and after and before != after
    assert str(tmp_path) not in before
    assert codeql.CodeQLEngine().input_fingerprint() == ""
