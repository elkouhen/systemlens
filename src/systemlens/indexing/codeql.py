"""Local CodeQL call-graph adapter for Java integration flows."""

import csv
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


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


_QLPACK = """name: systemlens/codeql-flow\nversion: 0.0.0\ndependencies:\n  codeql/java-all: \"*\"\n"""
_QUERY = """import java
import semmle.code.java.dispatch.VirtualDispatch

from MethodCall call, Callable enclosing, Method invoked, string dispatch_confidence
where enclosing = call.getEnclosingCallable() and
  (
    invoked = exactVirtualMethod(call) and dispatch_confidence = "exact"
    or
    not exists(exactVirtualMethod(call)) and
    invoked = viableCallable(call) and dispatch_confidence = "possible"
  )
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


@contextmanager
def automatic_codeql_database(repo_root: Path) -> Iterator[Path | None]:
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
            f"--source-root={repo_root.resolve()}", "--build-mode=none",
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=600, check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL source-only database creation failed: {detail}")
        yield database


def extract_codeql_calls(database: Path) -> list[CodeQLCall]:
    """Return statically resolved Java method calls from one CodeQL database."""
    if not database.is_dir():
        raise CodeQLError(f"CodeQL database not found: {database}")
    with tempfile.TemporaryDirectory(prefix="systemlens-codeql-") as directory:
        work = Path(directory)
        query = work / "calls.ql"
        query.write_text(_QUERY, encoding="utf-8")
        (work / "qlpack.yml").write_text(_QLPACK, encoding="utf-8")
        bqrs = work / "calls.bqrs"
        output = work / "calls.csv"
        # A query outside a named CodeQL pack cannot import the Java library.
        # Resolve its lock file in the temporary directory, so the installed
        # CodeQL package set is reproducible for this single analysis and no
        # repository state is modified.
        pack_install = subprocess.run(
            ["codeql", "pack", "install"], cwd=work,
            capture_output=True, text=True, timeout=180, check=False,
        )
        if pack_install.returncode != 0:
            detail = (pack_install.stderr or pack_install.stdout).strip()
            raise CodeQLError(f"CodeQL Java pack resolution failed: {detail}")
        command = [
            "codeql", "query", "run", str(query), f"--database={database}",
            f"--output={bqrs}",
        ]
        # The ad-hoc query lives in a fresh temporary pack.  Make an already
        # installed user pack cache visible to CodeQL's dependency resolver;
        # this only selects local packs and never invokes `codeql pack install`.
        user_packs = Path.home() / ".codeql" / "packages"
        if user_packs.is_dir():
            command.append(f"--search-path={user_packs}")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL call graph failed: {detail}")
        decoded = subprocess.run(
            ["codeql", "bqrs", "decode", str(bqrs), "--format=csv", f"--output={output}"],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if decoded.returncode != 0:
            detail = (decoded.stderr or decoded.stdout).strip()
            raise CodeQLError(f"CodeQL call graph decoding failed: {detail}")
        try:
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise CodeQLError("CodeQL did not produce a CSV call graph.") from exc
    calls: list[CodeQLCall] = []
    for row in rows:
        try:
            calls.append(CodeQLCall(
                caller=row["caller"], caller_path=row["caller_path"],
                caller_line=int(row["caller_line"]), callee=row["callee"],
                callee_path=row["callee_path"], callee_line=int(row["callee_line"]),
                call_line=int(row["call_line"]),
                dispatch_confidence=row.get("dispatch_confidence", "exact"),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CodeQLError("CodeQL returned an unexpected call-graph CSV schema.") from exc
    return calls
