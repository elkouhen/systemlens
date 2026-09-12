"""Optional CodeQL call-graph adapter for Java integration flows.

The caller supplies an already-built Java database. SystemLens never builds a
database, downloads packs, or persists an absolute database location: those
actions are environment-specific and can require a repository build.
"""

import csv
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeQLCall:
    caller: str
    caller_path: str
    caller_line: int
    callee: str
    callee_path: str
    callee_line: int
    call_line: int


class CodeQLError(RuntimeError):
    pass


_QLPACK = """name: systemlens/codeql-flow\nversion: 0.0.0\ndependencies:\n  codeql/java-all: \"*\"\n"""
_QUERY = """import java

from MethodAccess call, Method caller, Method callee
where caller = call.getEnclosingCallable() and callee = call.getMethod()
select caller.getQualifiedName() as caller,
  caller.getFile().getRelativePath() as caller_path,
  caller.getLocation().getStartLine() as caller_line,
  callee.getQualifiedName() as callee,
  callee.getFile().getRelativePath() as callee_path,
  callee.getLocation().getStartLine() as callee_line,
  call.getLocation().getStartLine() as call_line
"""


def extract_codeql_calls(database: Path) -> list[CodeQLCall]:
    """Return statically resolved Java method calls from one CodeQL database."""
    if not database.is_dir():
        raise CodeQLError(f"CodeQL database not found: {database}")
    with tempfile.TemporaryDirectory(prefix="systemlens-codeql-") as directory:
        work = Path(directory)
        query = work / "calls.ql"
        query.write_text(_QUERY, encoding="utf-8")
        (work / "qlpack.yml").write_text(_QLPACK, encoding="utf-8")
        output = work / "calls.csv"
        completed = subprocess.run(
            ["codeql", "database", "analyze", str(database), str(query),
             "--format=csv", f"--output={output}"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL call graph failed: {detail}")
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
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CodeQLError("CodeQL returned an unexpected call-graph CSV schema.") from exc
    return calls
