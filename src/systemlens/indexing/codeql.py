"""Local CodeQL call-graph adapter for Java integration flows."""

import csv
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


@dataclass(frozen=True)
class CodeQLCall:
    caller: str
    caller_path: str
    caller_line: int
    callee: str
    callee_path: str
    callee_line: int
    call_line: int
    dispatch_confidence: str = "exact"


class CodeQLError(RuntimeError):
    pass


# Keep the query pack reproducible and offline during indexing.  Users install
# this one dependency as part of provisioning CodeQL; indexing must never
# download or upgrade an analyzer package implicitly.
CODEQL_JAVA_PACK_VERSION = "9.3.0"
_QLPACK = (
    "name: systemlens/codeql-flow\nversion: 0.0.0\ndependencies:\n"
    f"  codeql/java-all: \"{CODEQL_JAVA_PACK_VERSION}\"\n"
)
_QUERY = """import java
import semmle.code.java.dispatch.VirtualDispatch

/**
 * Keep the call graph scoped to application source.  The source-only
 * database can still contain calls originating in extracted dependencies;
 * they cannot join SystemLens' source-backed method inventory and only add
 * work to the dispatch predicates.
 */
class SourceMethodCall extends MethodCall {
  SourceMethodCall() { this.getEnclosingCallable().fromSource() }
}

/** Compute the exact-dispatch relation once as a tightly bound predicate. */
predicate exactTarget(MethodCall call, Method target) {
  target = exactVirtualMethod(call)
}

/**
 * Prefer the unique target.  Only calls with no exact target reach the more
 * expensive viable-dispatch relation; this also avoids evaluating
 * exactVirtualMethod twice in the main result predicate.
 */
predicate resolvedTarget(MethodCall call, Method target, string confidence) {
  exactTarget(call, target) and confidence = "exact"
  or
  not exists(Method exact | exactTarget(call, exact)) and
  target = viableCallable(call) and confidence = "possible"
}

from SourceMethodCall call, Callable enclosing, Method invoked, string dispatch_confidence
where enclosing = call.getEnclosingCallable() and
  resolvedTarget(call, invoked, dispatch_confidence)
select enclosing.getQualifiedName() as caller,
  enclosing.getFile().getRelativePath() as caller_path,
  enclosing.getLocation().getStartLine() as caller_line,
  invoked.getQualifiedName() as callee,
  invoked.getFile().getRelativePath() as callee_path,
  invoked.getLocation().getStartLine() as callee_line,
  call.getLocation().getStartLine() as call_line,
  dispatch_confidence
"""


def codeql_executable() -> str | None:
    """Return the local CodeQL executable, when it is available."""
    return shutil.which("codeql")


def _run_with_progress(
    command: list[str],
    *,
    timeout: int,
    progress: Callable[[str], None] | None,
) -> subprocess.CompletedProcess[str]:
    """Run CodeQL while optionally forwarding its combined output live."""
    if progress is None:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    output: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        output.append(line)
        progress(line.rstrip())
    returncode = process.wait(timeout=timeout)
    return subprocess.CompletedProcess(command, returncode, "".join(output), "")


@contextmanager
def automatic_codeql_database(
    repo_root: Path, timeout_seconds: int = 600, threads: int = 1,
    ram_mb: int | None = None, verbosity: str | None = None,
    progress: Callable[[str], None] | None = None,
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
        command = [
            executable, "database", "create", str(database), "--language=java",
            f"--source-root={repo_root.resolve()}", "--build-mode=none", f"--threads={threads}",
        ]
        if verbosity is not None:
            command.append(f"--verbosity={verbosity}")
        if ram_mb is not None:
            command.append(f"--ram={ram_mb}")
        completed = _run_with_progress(
            command, timeout=timeout_seconds, progress=progress,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL source-only database creation failed: {detail}")
        yield database


def extract_codeql_calls(
    database: Path,
    executable: str | None = None,
    timeout_seconds: int = 600,
    path_prefix: str = "",
    threads: int = 1,
    ram_mb: int | None = None,
    verbosity: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[CodeQLCall]:
    """Return statically resolved Java method calls from one CodeQL database.

    A database created for a build module reports paths relative to that
    module. ``path_prefix`` maps those paths back to the indexed repository
    root so that calls from several module databases can be joined together.
    """
    if not database.is_dir():
        raise CodeQLError(f"CodeQL database not found: {database}")
    executable = executable or codeql_executable()
    if executable is None:
        raise CodeQLError("CodeQL executable not found.")
    with tempfile.TemporaryDirectory(prefix="systemlens-codeql-") as directory:
        work = Path(directory)
        query = work / "calls.ql"
        query.write_text(_QUERY, encoding="utf-8")
        (work / "qlpack.yml").write_text(_QLPACK, encoding="utf-8")
        bqrs = work / "calls.bqrs"
        output = work / "calls.csv"
        command = [
            executable, "query", "run", str(query), f"--database={database}",
            f"--output={bqrs}", f"--threads={threads}",
        ]
        if verbosity is not None:
            command.append(f"--verbosity={verbosity}")
        if ram_mb is not None:
            command.append(f"--ram={ram_mb}")
        # The ad-hoc query lives in a fresh temporary pack.  Make an already
        # installed user pack cache visible to CodeQL's dependency resolver.
        # The exact qlpack dependency above means this is an offline lookup;
        # absence of the pack produces an actionable CodeQL error.
        user_packs = Path.home() / ".codeql" / "packages"
        if user_packs.is_dir():
            command.append(f"--additional-packs={user_packs}")
        completed = _run_with_progress(
            command, timeout=timeout_seconds, progress=progress,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL call graph failed: {detail}")
        decoded = subprocess.run(
            [executable, "bqrs", "decode", str(bqrs), "--format=csv", f"--output={output}"],
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        if decoded.returncode != 0:
            detail = (decoded.stderr or decoded.stdout).strip()
            raise CodeQLError(f"CodeQL call graph decoding failed: {detail}")
        try:
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise CodeQLError("CodeQL did not produce a CSV call graph.") from exc
    normalized_prefix = path_prefix.strip("/")

    def repository_path(path: str) -> str:
        if not normalized_prefix or not path:
            return path
        return f"{normalized_prefix}/{path.lstrip('/')}"

    calls: list[CodeQLCall] = []
    for row in rows:
        try:
            calls.append(CodeQLCall(
                caller=row["caller"], caller_path=repository_path(row["caller_path"]),
                caller_line=int(row["caller_line"]), callee=row["callee"],
                callee_path=repository_path(row["callee_path"]), callee_line=int(row["callee_line"]),
                call_line=int(row["call_line"]),
                dispatch_confidence=row.get("dispatch_confidence", "exact"),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CodeQLError("CodeQL returned an unexpected call-graph CSV schema.") from exc
    return calls
