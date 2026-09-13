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
