"""Build-tool source generation and bounded process execution.

Generated sources (OpenAPI interfaces, AsyncAPI DTOs) are inputs of every
call-graph engine, so generation lives outside any engine.
"""

import errno
import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Timer


class SourceGenerationError(RuntimeError):
    """Maven or Gradle could not generate sources."""


def remaining_timeout(timeout_seconds: float, deadline: float | None) -> float:
    if deadline is None:
        return timeout_seconds
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(["process"], timeout_seconds)
    return min(timeout_seconds, remaining)


def run_with_progress(
    command: list[str],
    *,
    timeout: float,
    progress: Callable[[str], None] | None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a process with one timeout and process-group cleanup policy.

    ``env`` adds variables to the inherited environment, such as JVM options.
    """
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if progress is not None else subprocess.PIPE,
        text=True, bufsize=1, cwd=cwd, start_new_session=os.name == "posix",
        env={**os.environ, **env} if env else None,
    )

    def stop() -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        except PermissionError:
            # A process can exit between poll() and killpg(), or the platform
            # can reject the group operation during interpreter shutdown. Fall
            # back to the direct child and never turn cleanup into an indexing
            # failure.
            try:
                process.kill()
            except ProcessLookupError:
                pass
        except OSError as exc:
            if exc.errno not in {errno.ESRCH, errno.EPERM}:
                raise

    if progress is None:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stop()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                command, timeout, output=stdout, stderr=stderr
            ) from exc
        finally:
            stop()
            process.wait()
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    output: list[str] = []
    expired = False

    def expire() -> None:
        nonlocal expired
        expired = True
        stop()

    timer = Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            output.append(line)
            progress(line.rstrip())
        returncode = process.wait()
        if expired:
            raise subprocess.TimeoutExpired(command, timeout, output="".join(output))
    finally:
        timer.cancel()
        timer.join()
        stop()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
    return subprocess.CompletedProcess(command, returncode, "".join(output), "")


_GENERATION_SKIPPED_PARTS = frozenset({".git", ".systemlens", "target", "build", "out"})
_GRADLE_DESCRIPTORS = (
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
)


def _descriptor_directories(workspace: Path, name: str) -> set[Path]:
    return {
        path.parent
        for path in workspace.rglob(name)
        if path.is_file()
        and not _GENERATION_SKIPPED_PARTS.intersection(path.relative_to(workspace).parts)
    }


def _outermost(directories: set[Path]) -> list[Path]:
    ordered = sorted(directories, key=lambda path: (len(path.parts), str(path)))
    return [
        candidate
        for candidate in ordered
        if not any(parent in directories for parent in candidate.parents)
    ]


def generation_roots(workspace: Path) -> list[Path]:
    """Return outermost Maven or Gradle projects that can generate sources.

    A workspace-level descriptor owns its nested modules. When it is absent,
    independent module descriptors are generated from their own directories.
    This keeps multi-module repositories supported without requiring a synthetic
    aggregator at the repository root.
    """
    if (workspace / "pom.xml").is_file() or (workspace / "gradlew").is_file():
        return [workspace]

    descriptors = _descriptor_directories(workspace, "pom.xml")
    if not descriptors:
        descriptors = _descriptor_directories(workspace, "gradlew")
    return _outermost(descriptors)


def skipped_gradle_projects(workspace: Path, roots: list[Path]) -> list[Path]:
    """Outermost Gradle projects that no generation root covers.

    A Gradle project generates only through its own ``gradlew``; one
    without it, or one beside Maven roots, is reported instead of being
    skipped silently.
    """
    gradle = set().union(
        *(_descriptor_directories(workspace, name) for name in _GRADLE_DESCRIPTORS)
    )
    return [
        project
        for project in _outermost(gradle)
        if not any(root == project or root in project.parents for root in roots)
    ]


def _generation_command(root: Path) -> list[str] | None:
    """The source-generation command of a root, preferring its wrapper."""
    if (root / "pom.xml").is_file():
        maven = "./mvnw" if (root / "mvnw").is_file() else "mvn"
        return [maven, "-B", "-ntp", "-o", "generate-sources"]
    if (root / "gradlew").is_file():
        return ["./gradlew", "--no-daemon", "generateSources"]
    return None


def generate_sources(
    workspace: Path, *, timeout: float, progress: Callable[[str], None] | None,
) -> None:
    """Run build-tool source generation only, never compilation or tests.

    Every failure, including a missing or non-executable build tool and the
    timeout, is a :class:`SourceGenerationError` naming its cause.
    """
    deadline = time.monotonic() + timeout
    roots = generation_roots(workspace)
    if progress is not None:
        for project in skipped_gradle_projects(workspace, roots):
            relative = project.relative_to(workspace).as_posix()
            where = "à la racine" if relative == "." else relative
            progress(
                "→ Indexation : génération des sources ignorée pour le projet "
                f"Gradle {where} : aucun gradlew ne le génère."
            )
    for root in roots:
        command = _generation_command(root)
        if command is None:
            continue
        try:
            completed = run_with_progress(
                command,
                timeout=remaining_timeout(timeout, deadline),
                progress=progress,
                cwd=root,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceGenerationError(
                f"Source generation timed out after {timeout:g} s in {root}"
            ) from exc
        except OSError as exc:
            raise SourceGenerationError(
                f"Source generation could not run {command[0]} in {root}: "
                f"{exc.strerror or exc}"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise SourceGenerationError(f"Source generation failed in {root}: {detail}")
