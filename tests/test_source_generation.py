"""Source generation failures and tool choice (``backlog-copilot.md`` BC-05).

With ``--generate-sources``, a missing or non-executable build tool and the
timeout used to escape as ``FileNotFoundError``, ``PermissionError`` or
``TimeoutExpired`` tracebacks. Each one is now a ``SourceGenerationError``
that the CLI prints as ``Indexation impossible`` with its cause.
"""

import os
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from typer.testing import CliRunner

from systemlens.delivery.cli import app
from systemlens.indexing import source_generation
from systemlens.indexing.source_generation import SourceGenerationError, generate_sources

POM = "<project><modelVersion>4.0.0</modelVersion><groupId>e</groupId>" \
    "<artifactId>svc</artifactId><version>1</version></project>"
posix_only = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="needs POSIX permissions and a non-root user",
)


def _gradle(root: Path, wrapper: str | None, mode: int = 0o755) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
    if wrapper is not None:
        (root / "gradlew").write_text(wrapper, encoding="utf-8")
        (root / "gradlew").chmod(mode)


def test_a_missing_maven_is_a_source_generation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pom.xml").write_text(POM, encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    with pytest.raises(SourceGenerationError, match="could not run mvn"):
        generate_sources(tmp_path, timeout=10, progress=None)


@posix_only
def test_a_non_executable_gradlew_is_a_source_generation_error(tmp_path: Path) -> None:
    _gradle(tmp_path, "#!/bin/sh\nexit 0\n", mode=0o644)

    with pytest.raises(SourceGenerationError, match=r"could not run \./gradlew"):
        generate_sources(tmp_path, timeout=10, progress=None)


@posix_only
@pytest.mark.parametrize("progress", [None, lambda _line: None])
def test_a_generation_timeout_is_a_source_generation_error(tmp_path: Path, progress) -> None:
    _gradle(tmp_path, "#!/bin/sh\nsleep 30\n")

    with pytest.raises(SourceGenerationError, match="timed out after 0.5 s"):
        generate_sources(tmp_path, timeout=0.5, progress=progress)


@posix_only
def test_the_cli_prints_the_generation_failure_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gradle(tmp_path, "#!/bin/sh\nexit 0\n", mode=0o644)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["index", "--full", "--generate-sources"])

    assert result.exit_code == 2
    assert "Indexation impossible : Source generation could not run ./gradlew" in result.output
    assert "Permission denied" in result.output
    assert not isinstance(result.exception, PermissionError)


def test_maven_generation_prefers_the_project_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pom.xml").write_text(POM, encoding="utf-8")
    (tmp_path / "mvnw").write_text("#!/bin/sh\n", encoding="utf-8")
    observed: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        observed.append(command)
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(source_generation, "run_with_progress", run)
    generate_sources(tmp_path, timeout=10, progress=None)

    assert observed == [["./mvnw", "-B", "-ntp", "-o", "generate-sources"]]


def test_a_gradle_project_without_gradlew_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gradle(tmp_path / "orders", "#!/bin/sh\n")
    _gradle(tmp_path / "legacy/app", None)
    (tmp_path / "legacy/settings.gradle").write_text("include 'app'\n", encoding="utf-8")
    observed: list[Path | None] = []
    messages: list[str] = []

    def run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        observed.append(kwargs.get("cwd"))  # type: ignore[arg-type]
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(source_generation, "run_with_progress", run)
    generate_sources(tmp_path, timeout=10, progress=messages.append)

    assert observed == [tmp_path / "orders"]
    assert messages == [
        "→ Indexation : génération des sources ignorée pour le projet "
        "Gradle legacy : aucun gradlew ne le génère."
    ]
