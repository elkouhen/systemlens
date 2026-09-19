"""Local Joern call-graph adapter for Java integration flows.

Joern is optional.  It creates a temporary Java Code Property Graph (CPG) and
uses its non-interactive Scala interpreter to export only resolved calls.  The
returned shape intentionally matches the CodeQL adapter so flow materialization
keeps one conservative, engine-neutral contract.
"""

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from systemlens.indexing.codeql import CodeQLCall


class JoernError(RuntimeError):
    pass


# ``callee`` is Joern's resolved call-graph traversal. Unresolved calls are
# deliberately omitted: receiver-type expansion and ``methodFullName`` joins
# are useful diagnostics, but are not source-proven call edges. Tab-separated
# output is supported by every Joern shell version; source evidence paths
# cannot contain tabs.
_CALLS_SCRIPT = r'''@main def exec(cpgFile: String, outFile: String) = {
  importCpg(cpgFile)
  val rows = cpg.call.flatMap { call =>
    call.method.headOption.flatMap { caller =>
      val resolved = call.callee.filterNot(_.isExternal).toList
      val encoded = resolved.map { callee =>
        List(caller.fullName, caller.filename, caller.lineNumber.getOrElse(0).toString,
          callee.fullName, callee.filename, callee.lineNumber.getOrElse(0).toString,
          call.lineNumber.getOrElse(0).toString, "exact").mkString("\t")
      }
    }
  }.toList.sorted
  rows.mkString("\n") #> outFile
}'''


def joern_executable() -> str | None:
    """Return the Joern interpreter when the complete local CLI is present."""
    executable = shutil.which("joern")
    return executable if executable and shutil.which("joern-parse") else None


@contextmanager
def automatic_joern_cpg(
    repo_root: Path, timeout_seconds: int = 600,
) -> Iterator[Path | None]:
    """Create a temporary Java source CPG for one source-owning project."""
    executable = joern_executable()
    if executable is None:
        yield None
        return
    parser = shutil.which("joern-parse")
    assert parser is not None
    with tempfile.TemporaryDirectory(prefix="systemlens-joern-cpg-") as directory:
        cpg = Path(directory) / "java.cpg.bin"
        command = [
            parser, str(repo_root.resolve()), "--language", "JAVASRC",
            "--output", str(cpg), "--frontend-args", "--enable-type-recovery",
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise JoernError(f"Joern Java CPG creation failed: {detail}")
        yield cpg


def extract_joern_calls(
    cpg: Path,
    executable: str | None = None,
    timeout_seconds: int = 600,
    path_prefix: str = "",
    source_root: Path | None = None,
) -> list[CodeQLCall]:
    """Return resolved Java calls from a local Joern CPG.

    Joern's ``callee`` traversal performs the resolution.  The adapter labels
    those rows ``exact`` and intentionally drops unresolved call sites.
    """
    if not cpg.is_file():
        raise JoernError(f"Joern CPG not found: {cpg}")
    executable = executable or joern_executable()
    if executable is None:
        raise JoernError("Joern executable (and joern-parse) not found.")
    with tempfile.TemporaryDirectory(prefix="systemlens-joern-query-") as directory:
        work = Path(directory)
        script = work / "calls.sc"
        output = work / "calls.tsv"
        script.write_text(_CALLS_SCRIPT, encoding="utf-8")
        completed = subprocess.run(
            [
                executable, "--script", str(script),
                "--param", f"cpgFile={cpg}", "--param", f"outFile={output}",
            ],
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise JoernError(f"Joern call graph failed: {detail}")
        try:
            rows = output.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise JoernError("Joern did not produce a call-graph export.") from exc

    normalized_prefix = path_prefix.strip("/")

    def repository_path(path: str) -> str:
        candidate = Path(path)
        if source_root is not None and candidate.is_absolute():
            try:
                path = candidate.resolve().relative_to(source_root.resolve()).as_posix()
            except ValueError:
                # A generated or third-party source path cannot prove an
                # indexed source location; leave it untouched for the later
                # unique-signature fallback.
                pass
        if not normalized_prefix or not path:
            return path
        return f"{normalized_prefix}/{path.lstrip('/')}"

    calls: list[CodeQLCall] = []
    for row in rows:
        if not row.strip():
            continue
        values = row.split("\t")
        if len(values) != 8:
            raise JoernError("Joern returned an unexpected call-graph TSV schema.")
        try:
            calls.append(CodeQLCall(
                caller=values[0], caller_path=repository_path(values[1]),
                caller_line=int(values[2]), callee=values[3],
                callee_path=repository_path(values[4]), callee_line=int(values[5]),
                call_line=int(values[6]), dispatch_confidence=values[7],
            ))
        except ValueError as exc:
            raise JoernError("Joern returned invalid call-graph source locations.") from exc
    return calls
