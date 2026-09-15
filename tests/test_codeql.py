from pathlib import Path
from subprocess import CompletedProcess

from systemlens.indexing import codeql


def test_query_scopes_source_calls_and_folds_dispatch_resolution() -> None:
    assert "class SourceMethodCall extends MethodCall" in codeql._QUERY
    assert "this.getEnclosingCallable().fromSource()" in codeql._QUERY
    assert "predicate exactTarget(MethodCall call, Method target)" in codeql._QUERY
    assert "predicate resolvedTarget(MethodCall call, Method target, string confidence)" in codeql._QUERY
    assert codeql._QUERY.count("exactVirtualMethod(call)") == 1
    assert "not exists(Method exact | exactTarget(call, exact))" in codeql._QUERY


def test_automatic_codeql_database_is_source_only_and_temporary(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[tuple[list[str], int | None]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        commands.append((command, _kwargs.get("timeout")))
        Path(command[3]).mkdir()
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(codeql.subprocess, "run", run)

    with codeql.automatic_codeql_database(
        tmp_path, timeout_seconds=42, threads=4, ram_mb=4096
    ) as database:
        assert database is not None
        assert database.is_dir()
        database_path = database

    assert not database_path.exists()
    assert commands == [([
        "codeql", "database", "create", str(database_path), "--language=java",
        f"--source-root={tmp_path.resolve()}", "--build-mode=none", "--threads=4", "--ram=4096",
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

    monkeypatch.setattr(codeql.subprocess, "run", run)

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

    monkeypatch.setattr(codeql.subprocess, "run", run)

    calls = codeql.extract_codeql_calls(
        database, executable="custom-codeql", path_prefix="module-a"
    )

    assert calls[0].caller_path == "module-a/src/main/java/A.java"
    assert calls[0].callee_path == "module-a/src/main/java/B.java"
