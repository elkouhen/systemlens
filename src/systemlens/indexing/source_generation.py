"""Build-tool source generation and bounded process execution.

Generated sources (OpenAPI interfaces, AsyncAPI DTOs) are inputs of every
call-graph engine, so generation lives outside any engine.
"""

import errno
import os
import shutil
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


def prepare_generation_workspace(repo_root: Path, destination: Path) -> None:
    """Copy the project to a disposable workspace before source generation."""
    shutil.copytree(
        repo_root,
        destination,
        ignore=shutil.ignore_patterns(
            ".git", ".systemlens", ".venv", "node_modules", "target", "build", "out"
        ),
    )


def generation_roots(workspace: Path) -> list[Path]:
    """Return outermost Maven or Gradle projects that can generate sources.

    A workspace-level descriptor owns its nested modules. When it is absent,
    independent module descriptors are generated from their own directories.
    This keeps multi-module repositories supported without requiring a synthetic
    aggregator at the repository root.
    """
    if (workspace / "pom.xml").is_file() or (workspace / "gradlew").is_file():
        return [workspace]

    descriptors = [
        path.parent
        for path in workspace.rglob("pom.xml")
        if not any(part in {".git", ".systemlens", "target", "build", "out"}
                   for part in path.relative_to(workspace).parts)
    ]
    if descriptors:
        candidates = sorted(set(descriptors), key=lambda path: (len(path.parts), str(path)))
    else:
        candidates = sorted(
            {
                path.parent
                for path in workspace.rglob("gradlew")
                if path.is_file()
                and not any(
                    part in {".git", ".systemlens", "target", "build", "out"}
                    for part in path.relative_to(workspace).parts
                )
            },
            key=lambda path: (len(path.parts), str(path)),
        )
    return [
        candidate
        for candidate in candidates
        if not any(parent in candidates for parent in candidate.parents)
    ]


def generate_sources(
    workspace: Path, *, timeout: float, progress: Callable[[str], None] | None,
) -> None:
    """Run build-tool source generation only, never compilation or tests."""
    deadline = time.monotonic() + timeout
    roots = generation_roots(workspace)
    for root in roots:
        if (root / "pom.xml").is_file():
            command = ["mvn", "-B", "-ntp", "-o", "generate-sources"]
        elif (root / "gradlew").is_file():
            command = ["./gradlew", "--no-daemon", "generateSources"]
        else:
            continue
        completed = run_with_progress(
            command,
            timeout=remaining_timeout(timeout, deadline),
            progress=progress,
            cwd=root,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise SourceGenerationError(f"Source generation failed in {root}: {detail}")
