"""CodeQL executable, database lifecycle and query execution.

This module owns every subprocess the engine starts. Tests that need to
observe or replace a CodeQL process patch the names defined here.
"""

import csv
import hashlib
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from systemlens.engines.codeql.errors import CodeQLError
from systemlens.engines.codeql.queries import _QLPACK
from systemlens.indexing.source_projection import (
    executable_identity,
    project_java_sources as _prepare_source_only_root,
    projection_fingerprint,
)
from systemlens.indexing.source_generation import (
    generate_sources as _generate_sources,
    prepare_generation_workspace as _prepare_generation_workspace,
    remaining_timeout as _remaining_timeout,
    run_with_progress as _run_with_progress,
)


def codeql_executable() -> str | None:
    """Return the local CodeQL executable, when it is available."""
    return shutil.which("codeql")


_SOURCE_PREFIX = re.compile(r"^sourceLocationPrefix:\s*(.+?)\s*$", re.MULTILINE)


def database_fingerprint(database: Path) -> str:
    """Identify one CodeQL database without naming its local path.

    The metadata file records the creation time and the source root, so a
    recreated database gets a new identity even when its path is reused.
    """
    try:
        content = (database / "codeql-database.yml").read_bytes()
    except OSError:
        # No identity to record: opening the database refuses it anyway.
        return ""
    return hashlib.sha256(content).hexdigest()[:24]


def _archive_name(prefix: str, relative: str) -> str:
    """Spell a source path the way CodeQL stores it in ``src.zip``."""
    # CodeQL archives the absolute path without its root, and a Windows
    # drive ``C:`` as ``C_``.
    normalized = prefix.replace("\\", "/").rstrip("/")
    normalized = re.sub(r"^([A-Za-z]):", r"\1_", normalized).lstrip("/")
    return f"{normalized}/{relative}" if normalized else relative


def verify_database_sources(
    database: Path, repo_root: Path, paths: Sequence[str],
) -> int:
    """Refuse a supplied database that does not represent the indexed sources.

    Every indexed Java file that CodeQL archived must have the same content
    in the database's source archive as on disk: a database built from
    another revision would place old call lines in other methods. When the
    archive holds none of the indexed Java files, the database was built
    from another source root and its rows cannot join. CodeQL skips some
    files, such as ``package-info.java``, so a file absent from the archive
    is not a mismatch by itself. Returns the number of files compared.
    """
    java_paths = sorted(path for path in paths if path.endswith(".java"))
    if not java_paths:
        return 0
    metadata = database / "codeql-database.yml"
    try:
        match = _SOURCE_PREFIX.search(metadata.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CodeQLError(f"CodeQL database metadata not found: {metadata}") from exc
    if match is None:
        raise CodeQLError(f"CodeQL database metadata has no source root: {metadata}")
    prefix = match.group(1).strip("'\"")
    archive = database / "src.zip"
    directory = database / "src"
    compared = 0
    stale: list[str] = []

    def check(relative: str, archived: bytes) -> None:
        nonlocal compared
        compared += 1
        try:
            current = (repo_root / relative).read_bytes()
        except OSError:
            return
        if current != archived:
            stale.append(relative)

    if archive.is_file():
        try:
            with zipfile.ZipFile(archive) as handle:
                names = set(handle.namelist())
                for relative in java_paths:
                    name = _archive_name(prefix, relative)
                    if name in names:
                        check(relative, handle.read(name))
        except (OSError, zipfile.BadZipFile) as exc:
            raise CodeQLError(f"CodeQL source archive is unreadable: {archive}") from exc
    elif directory.is_dir():
        for relative in java_paths:
            candidate = directory / _archive_name(prefix, relative)
            if candidate.is_file():
                check(relative, candidate.read_bytes())
    else:
        raise CodeQLError(
            f"CodeQL database has no source archive to check against the indexed sources: {database}"
        )
    if compared == 0:
        raise CodeQLError(
            "The CodeQL database does not contain the indexed Java sources: it was "
            "built from another source root. Recreate it with --source-root at the "
            "indexed repository root."
        )
    if stale:
        shown = ", ".join(stale[:5]) + (f" and {len(stale) - 5} more" if len(stale) > 5 else "")
        raise CodeQLError(
            f"The CodeQL database is stale: {len(stale)} indexed Java file(s) changed "
            f"since it was created ({shown}). Recreate the database from the current sources."
        )
    return compared


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
    """Yield a source-only Java database of the indexed sources.

    CodeQL is an optional local prerequisite.  Its absence leaves the AST-only
    flow materialization available; a present but failing CodeQL installation
    is reported as an indexing error rather than silently reducing coverage.

    The database is cached under ``.systemlens/codeql/`` with a fingerprint
    of the projected sources and of the CodeQL executable. A later run whose
    projection has the same fingerprint reuses it instead of creating it
    again; any change creates a new one. When the cache cannot be written,
    the database stays temporary.
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
        cache = repo_root / CODEQL_CACHE_DIRECTORY
        cached = cache / "java-database"
        stamp = cache / "java-database.fingerprint"
        fingerprint = _projection_fingerprint(source_root, executable)
        if _cached_database_matches(cached, stamp, fingerprint):
            if progress is not None:
                progress("CodeQL : base réutilisée, sources inchangées.")
            yield cached
            return
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
        yield _store_cached_database(database, cached, stamp, fingerprint)


CODEQL_CACHE_DIRECTORY = Path(".systemlens") / "codeql"


def _projection_fingerprint(source_root: Path, executable: str) -> str:
    """Hash the projected sources and the CodeQL executable that reads them."""
    return projection_fingerprint(
        source_root, "build-mode=none\n" + executable_identity(executable) + "\n",
    )


def _cached_database_matches(cached: Path, stamp: Path, fingerprint: str) -> bool:
    try:
        return (
            stamp.read_text(encoding="utf-8").strip() == fingerprint
            and (cached / "codeql-database.yml").is_file()
        )
    except OSError:
        return False


def _store_cached_database(
    database: Path, cached: Path, stamp: Path, fingerprint: str,
) -> Path:
    """Move a finalized database to the cache; return where it now lives.

    The stamp is removed first and written last, so an interrupted move
    leaves no stamp and the next run creates the database again.
    """
    if not (database / "codeql-database.yml").is_file():
        return database
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        stamp.unlink(missing_ok=True)
        if cached.exists():
            shutil.rmtree(cached)
        shutil.move(str(database), str(cached))
        stamp.write_text(fingerprint + "\n", encoding="utf-8")
    except OSError:
        return database if database.is_dir() else cached
    return cached


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


