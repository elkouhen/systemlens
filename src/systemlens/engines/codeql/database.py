"""CodeQL executable, database lifecycle and query execution.

This module owns every subprocess the engine starts. Tests that need to
observe or replace a CodeQL process patch the names defined here.
"""

import csv
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from systemlens.engines.codeql.errors import CodeQLError
from systemlens.engines.codeql.queries import _QLPACK
from systemlens.indexing.file_inventory import is_build_output
from systemlens.indexing.source_generation import (
    generate_sources as _generate_sources,
    prepare_generation_workspace as _prepare_generation_workspace,
    remaining_timeout as _remaining_timeout,
    run_with_progress as _run_with_progress,
)


def codeql_executable() -> str | None:
    """Return the local CodeQL executable, when it is available."""
    return shutil.which("codeql")


def _prepare_source_only_root(
    repo_root: Path, destination: Path, *, include_target_java_sources: bool = False,
    allowed_paths: Sequence[str] | None = None,
) -> int:
    """Copy application and generated Java sources without build descriptors.

    Maven projects may keep generated Java sources below ``target``. They are
    needed to resolve calls whose signatures use generated DTOs, while the rest
    of ``target`` remains an untrusted build artifact and is excluded unless
    source generation was explicitly requested for this index.
    """
    excluded_directories = {".git", ".systemlens"}
    allowed = set(allowed_paths or ())
    copied = 0
    for source in repo_root.rglob("*.java"):
        if not source.is_file():
            continue
        relative = source.relative_to(repo_root)
        if allowed and relative.as_posix() not in allowed:
            continue
        parts = relative.parts
        # Build directories are rooted at a module, never below ``src``: a
        # Java package named ``target`` or ``out`` is application source.
        build_directories = [
            index for index, part in enumerate(parts)
            if part in {"target", "out"} and "src" not in parts[:index]
        ]
        generated_target = any(
            parts[index:index + 2] == ("target", "generated-sources")
            for index in build_directories
        )
        target_java_source = generated_target
        if include_target_java_sources and any(parts[index] == "target" for index in build_directories):
            target_java_source = True
        if (
            any(part in excluded_directories for part in parts)
            or build_directories
            or is_build_output(relative.as_posix())
        ) and not target_java_source:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def _decode_bqrs(
    executable: str, bqrs: Path, output: Path, *, timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Decode a BQRS file with the same process-group timeout guarantees."""
    return _run_with_progress(
        [executable, "bqrs", "decode", str(bqrs), "--format=csv", f"--output={output}"],
        timeout=timeout,
        progress=None,
    )


@contextmanager
def automatic_codeql_database(
    repo_root: Path, timeout_seconds: int = 600, threads: int = 1,
    ram_mb: int | None = None, verbosity: str | None = None,
    progress: Callable[[str], None] | None = None,
    generate_sources: bool = False,
    include_target_java_sources: bool = False,
    allowed_paths: Sequence[str] | None = None,
    deadline: float | None = None,
) -> Iterator[Path | None]:
    """Create a temporary source-only Java database for one index run.

    CodeQL is an optional local prerequisite.  Its absence leaves the AST-only
    flow materialization available; a present but failing CodeQL installation
    is reported as an indexing error rather than silently reducing coverage.
    """
    executable = codeql_executable()
    if executable is None:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="systemlens-codeql-db-") as directory:
        database = Path(directory) / "database"
        source_root = Path(directory) / "source"
        generation_root = Path(directory) / "generated-project"
        codeql_input = repo_root
        if generate_sources:
            _prepare_generation_workspace(repo_root, generation_root)
            _generate_sources(
                generation_root,
                timeout=_remaining_timeout(timeout_seconds, deadline),
                progress=progress,
            )
            codeql_input = generation_root
        _prepare_source_only_root(
            codeql_input,
            source_root,
            include_target_java_sources=(
                include_target_java_sources or generate_sources
            ),
            allowed_paths=allowed_paths,
        )
        command = [
            executable, "database", "create", str(database), "--language=java",
            f"--source-root={source_root}", "--build-mode=none", f"--threads={threads}",
        ]
        if verbosity is not None:
            command.append(f"--verbosity={verbosity}")
        if ram_mb is not None:
            command.append(f"--ram={ram_mb}")
        completed = _run_with_progress(
            command, timeout=_remaining_timeout(timeout_seconds, deadline), progress=progress,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL source-only database creation failed: {detail}")
        yield database


def run_query(
    database: Path,
    name: str,
    query_text: str,
    *,
    executable: str | None = None,
    timeout_seconds: int = 600,
    threads: int = 1,
    ram_mb: int | None = None,
    verbosity: str | None = None,
    progress: Callable[[str], None] | None = None,
    deadline: float | None = None,
    label: str,
) -> list[dict[str, str]]:
    """Run one ad-hoc query against a database and return its CSV rows.

    The query lives in a fresh temporary pack pinned to the installed
    ``codeql/java-all`` version; an already installed user pack cache is made
    visible so the lookup stays offline. ``label`` names the query in errors.
    A deadline reached during the query raises ``TimeoutExpired`` with no
    rows: CodeQL writes its result file only when the query completes.
    """
    if not database.is_dir():
        raise CodeQLError(f"CodeQL database not found: {database}")
    executable = executable or codeql_executable()
    if executable is None:
        raise CodeQLError("CodeQL executable not found.")
    with tempfile.TemporaryDirectory(prefix=f"systemlens-codeql-{name}-") as directory:
        work = Path(directory)
        query = work / f"{name}.ql"
        query.write_text(query_text, encoding="utf-8")
        (work / "qlpack.yml").write_text(_QLPACK, encoding="utf-8")
        bqrs = work / f"{name}.bqrs"
        output = work / f"{name}.csv"
        command = [
            executable, "query", "run", str(query), f"--database={database}",
            f"--output={bqrs}", f"--threads={threads}",
        ]
        if verbosity is not None:
            command.append(f"--verbosity={verbosity}")
        if ram_mb is not None:
            command.append(f"--ram={ram_mb}")
        user_packs = Path.home() / ".codeql" / "packages"
        if user_packs.is_dir():
            command.append(f"--additional-packs={user_packs}")
        completed = _run_with_progress(
            command, timeout=_remaining_timeout(timeout_seconds, deadline), progress=progress,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL {label} failed: {detail}")
        decoded = _decode_bqrs(
            executable, bqrs, output,
            timeout=_remaining_timeout(timeout_seconds, deadline),
        )
        if decoded.returncode != 0:
            detail = (decoded.stderr or decoded.stdout).strip()
            raise CodeQLError(f"CodeQL {label} decoding failed: {detail}")
        try:
            return _read_rows(output)
        except OSError as exc:
            raise CodeQLError(f"CodeQL did not produce a CSV for the {label}.") from exc


def _read_rows(output: Path) -> list[dict[str, str]]:
    with output.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


