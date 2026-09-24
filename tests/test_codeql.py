from pathlib import Path
from subprocess import CompletedProcess
import os
import subprocess
import sys
import time

import pytest

from systemlens.domain.code_flows import IntegrationMethod
from systemlens.indexing import codeql


def test_query_scopes_source_calls_and_folds_dispatch_resolution() -> None:
    assert "class SourceMethodCall extends MethodCall" in codeql._QUERY
    assert "this.getEnclosingCallable().fromSource()" in codeql._QUERY
    assert "predicate exactTarget(MethodCall call, Method target)" in codeql._QUERY
    assert "predicate resolvedTarget(MethodCall call, Method target, string confidence)" in codeql._QUERY
    assert codeql._QUERY.count("exactVirtualMethod(call)") == 1
    assert "not exists(Method exact | exactTarget(call, exact))" in codeql._QUERY
    assert "target.fromSource()" in codeql._QUERY


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

    monkeypatch.setattr(codeql, "_run_with_progress", run)
    assert codeql.extract_codeql_reachability(tmp_path, methods, executable="codeql") == [
        codeql.CodeQLReachability("C.in", "C.java", 1, "C.out", "C.java", 2, "medium")
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


def test_generate_sources_runs_only_the_maven_generation_phase(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pom.xml").write_text("<project />", encoding="utf-8")
    observed: list[tuple[list[str], Path | None]] = []

    def run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        observed.append((command, kwargs.get("cwd")))
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql, "_run_with_progress", run)
    codeql._generate_sources(tmp_path, timeout=42, progress=None)

    assert observed == [(["mvn", "-B", "-ntp", "-o", "generate-sources"], tmp_path)]


def test_automatic_codeql_database_is_source_only_and_temporary(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[tuple[list[str], int | None]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        commands.append((command, _kwargs.get("timeout")))
        Path(command[3]).mkdir()
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(codeql, "_run_with_progress", run)

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


def test_automatic_codeql_database_skips_when_codeql_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(codeql, "codeql_executable", lambda: None)

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

    monkeypatch.setattr(codeql, "_run_with_progress", run)

    calls = codeql.extract_codeql_calls(
        database, executable="custom-codeql", timeout_seconds=42, threads=4, ram_mb=4096
    )

    assert calls == [codeql.CodeQLCall(
        "com.example.A.run", "A.java", 1, "com.example.B.send", "B.java", 2, 3,
    )]
    assert all("pack" not in command[1:3] for command, _timeout in commands)
    assert all(command[0] == "custom-codeql" for command, _timeout in commands)
    assert all(timeout == 42 for _command, timeout in commands)
    assert "--threads=4" in commands[0][0]
    assert "--ram=4096" in commands[0][0]


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

    monkeypatch.setattr(codeql, "_run_with_progress", run)

    types = codeql.extract_codeql_kafka_message_types(
        database, executable="custom-codeql", timeout_seconds=42, threads=4, ram_mb=4096
    )

    assert types == [codeql.CodeQLKafkaMessageType("Publisher.java", 7, "OrderCreated")]


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

    monkeypatch.setattr(codeql, "_run_with_progress", run)

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

    monkeypatch.setattr(codeql, "_run_with_progress", run)

    codeql.extract_codeql_calls(database, executable="custom-codeql", caller_prefix="payments")

    assert 'path = enclosing.getFile().getRelativePath() and path.regexpMatch("^payments/")' in queries[0]
