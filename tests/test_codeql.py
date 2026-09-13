from pathlib import Path
from subprocess import CompletedProcess

from systemlens.indexing import codeql


def test_automatic_codeql_database_is_source_only_and_temporary(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        commands.append(command)
        Path(command[3]).mkdir()
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(codeql.subprocess, "run", run)

    with codeql.automatic_codeql_database(tmp_path) as database:
        assert database is not None
        assert database.is_dir()
        database_path = database

    assert not database_path.exists()
    assert commands == [[
        "codeql", "database", "create", str(database_path), "--language=java",
        f"--source-root={tmp_path.resolve()}", "--build-mode=none",
    ]]


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
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        commands.append(command)
        if command[1:3] == ["bqrs", "decode"]:
            output = next(part.removeprefix("--output=") for part in command if part.startswith("--output="))
            Path(output).write_text(
                "caller,caller_path,caller_line,callee,callee_path,callee_line,call_line,dispatch_confidence\n"
                "com.example.A.run,A.java,1,com.example.B.send,B.java,2,3,exact\n",
                encoding="utf-8",
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codeql.subprocess, "run", run)

    calls = codeql.extract_codeql_calls(database, executable="custom-codeql")

    assert calls == [codeql.CodeQLCall(
        "com.example.A.run", "A.java", 1, "com.example.B.send", "B.java", 2, 3,
    )]
    assert all("pack" not in command[1:3] for command in commands)
    assert all(command[0] == "custom-codeql" for command in commands)
