from pathlib import Path
from subprocess import CompletedProcess

from systemlens.indexing import joern


def test_automatic_joern_cpg_uses_java_source_frontend_and_is_temporary(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[tuple[list[str], int | None]] = []

    def run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        commands.append((command, kwargs.get("timeout")))
        Path(command[command.index("--output") + 1]).touch()
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(joern, "joern_executable", lambda: "joern")
    monkeypatch.setattr(joern.shutil, "which", lambda value: "joern-parse" if value == "joern-parse" else "joern")
    monkeypatch.setattr(joern.subprocess, "run", run)

    with joern.automatic_joern_cpg(tmp_path, timeout_seconds=42) as cpg:
        assert cpg is not None
        assert cpg.is_file()
        cpg_path = cpg

    assert not cpg_path.exists()
    assert commands == [([
        "joern-parse", str(tmp_path.resolve()), "--language", "JAVASRC",
        "--output", str(cpg_path), "--frontend-args", "--enable-type-recovery",
    ], 42)]


def test_extract_joern_calls_parses_resolved_calls_and_prefixes_paths(
    tmp_path: Path, monkeypatch
) -> None:
    cpg = tmp_path / "java.cpg.bin"
    cpg.touch()

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        output = next(value.removeprefix("outFile=") for value in command if value.startswith("outFile="))
        Path(output).write_text(
            "com.example.A.run\tsrc/main/java/A.java\t1\tcom.example.B.send\tsrc/main/java/B.java\t2\t3\texact\n",
            encoding="utf-8",
        )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(joern.subprocess, "run", run)
    calls = joern.extract_joern_calls(
        cpg, executable="custom-joern", timeout_seconds=42, path_prefix="module-a"
    )

    assert calls == [joern.CodeQLCall(
        "com.example.A.run", "module-a/src/main/java/A.java", 1,
        "com.example.B.send", "module-a/src/main/java/B.java", 2, 3,
    )]
    assert "call.callee.filterNot(_.isExternal).toList" in joern._CALLS_SCRIPT
    assert '"possible"' not in joern._CALLS_SCRIPT
    assert "receiverTargets" not in joern._CALLS_SCRIPT
