"""Local CodeQL call-graph adapter for Java integration flows."""

import csv
import errno
import os
import re
import signal
import shutil
import subprocess
import tempfile
import time
from threading import Timer
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Mapping
from typing import Callable, Iterator, Sequence

from systemlens.domain.code_flows import IntegrationMethod
from systemlens.indexing.file_inventory import is_build_output


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


@dataclass(frozen=True)
class CodeQLReachability:
    """A direct CodeQL answer for one indexed input/output method pair."""

    source: str
    source_path: str
    source_line: int
    target: str
    target_path: str
    target_line: int
    confidence: str


@dataclass(frozen=True)
class CodeQLKafkaMessageType:
    """A source-backed payload type found at a Strategy1 Kafka send site."""

    path: str
    line: int
    message_type: str


class CodeQLError(RuntimeError):
    pass


class CodeQLTimeout(subprocess.TimeoutExpired):
    """A CodeQL deadline with any rows recovered from a completed result."""

    def __init__(self, command: list[str], timeout: float, *, calls: list[CodeQLCall] | None = None):
        super().__init__(command, timeout)
        self.calls = calls or []


def _remaining_timeout(timeout_seconds: float, deadline: float | None) -> float:
    if deadline is None:
        return timeout_seconds
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(["codeql"], timeout_seconds)
    return min(timeout_seconds, remaining)


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

/**
 * Resolve a call when CodeQL can identify one exact virtual-dispatch target.
 * The target must also be source-backed because the Python join needs a file
 * and line that exist in the indexed repository.
 */
predicate exactTarget(MethodCall call, Method target) {
  target = exactVirtualMethod(call) and target.fromSource()
}

/**
 * Pick one resolution strategy for each call.
 *
 * Exact dispatch has priority.  The possible-dispatch branches are evaluated
 * only when no exact target exists, so one call does not produce both an
 * exact edge and a wider set of possible edges.
 *
 * The final branch keeps the declared source method when a buildless database
 * cannot expose its concrete implementation.  Python may then add a
 * conservative source-backed bridge if it can prove one.
 */
predicate resolvedTarget(MethodCall call, Method target, string confidence) {
  exactTarget(call, target) and confidence = "exact"
  or
  not exists(Method exact | exactTarget(call, exact)) and
  target = viableCallable(call) and target.fromSource() and confidence = "possible"
  or
  // In buildless databases a virtual implementation can be unavailable even
  // though the source-declared interface method is indexed. Keep that
  // declared source method so the Python join can conservatively bridge it
  // to a unique source implementation carrying an integration endpoint.
  not exists(Method exact | exactTarget(call, exact)) and
  target = call.getMethod() and target.fromSource() and confidence = "possible"
}

predicate callerInScope(Callable enclosing) {
  __CALLER_SCOPE__
}

from SourceMethodCall call, Callable enclosing, Method invoked, string dispatch_confidence
where callerInScope(enclosing) and
  enclosing = call.getEnclosingCallable() and
  resolvedTarget(call, invoked, dispatch_confidence)
// Return source locations rather than database-internal IDs.  SystemLens uses
// these locations to join CodeQL rows to its indexed IntegrationMethod facts.
select enclosing.getQualifiedName() as caller,
  enclosing.getFile().getRelativePath() as caller_path,
  enclosing.getLocation().getStartLine() as caller_line,
  invoked.getQualifiedName() as callee,
  invoked.getFile().getRelativePath() as callee_path,
  invoked.getLocation().getStartLine() as callee_line,
  call.getLocation().getStartLine() as call_line,
  dispatch_confidence
"""

_KAFKA_MESSAGE_TYPES_QUERY = """import java
import semmle.code.java.dataflow.DataFlow

/**
 * Strategy1 names the topic convention, but the payload can be hidden behind
 * a local variable or a method parameter. CodeQL resolves that expression's
 * declared Java type without guessing from the topic name or serializer.
 */
predicate wrappedPayloadType(Expr payload, string messageType) {
  exists(ParameterizedType type |
    type = payload.getType() and
    (
      type.getGenericType().getName() = ["Message", "GenericMessage"] and
      messageType = type.getTypeArgument(0).getName() and
      not messageType in ["?", "Object"]
      or
      type.getGenericType().getName() = "ProducerRecord" and
      messageType = type.getTypeArgument(1).getName() and
      not messageType in ["?", "Object"]
    )
  )
}

predicate sourcePayloadType(Callable enclosing, Expr payload, string messageType) {
  exists(Expr source |
    source != payload and
    source.getEnclosingCallable() = enclosing and
    DataFlow::localFlow(DataFlow::exprNode(source), DataFlow::exprNode(payload)) and
    not source.getType().getName() in ["Object", "Message", "GenericMessage", "ProducerRecord"] and
    messageType = source.getType().getName()
  )
}

predicate inferredPayloadType(Callable enclosing, Expr payload, string messageType) {
  wrappedPayloadType(payload, messageType)
  or
  not exists(ParameterizedType type | type = payload.getType() and
    type.getGenericType().getName() = ["Message", "GenericMessage", "ProducerRecord"]
  ) and (
    sourcePayloadType(enclosing, payload, messageType)
    or
    not exists(string sourceType | sourcePayloadType(enclosing, payload, sourceType)) and
    messageType = payload.getType().getName()
  )
}

from MethodCall call, Callable enclosing, Expr payload, string messageType
where call.getMethod().getName().regexpMatch("^envoyerMessageKafka.*")
  and enclosing = call.getEnclosingCallable()
  and enclosing.fromSource()
  and payload = call.getArgument(1)
  and inferredPayloadType(enclosing, payload, messageType)
select call.getFile().getRelativePath() as path,
  call.getLocation().getStartLine() as line,
  messageType as message_type
"""


def _calls_query(caller_prefix: str) -> str:
    normalized_prefix = caller_prefix.strip("/")
    if not normalized_prefix:
        scope = "exists(Callable candidate | candidate = enclosing)"
    else:
        pattern = _ql_string(f"^{re.escape(normalized_prefix)}/")
        scope = (
            "exists(string path | "
            "path = enclosing.getFile().getRelativePath() and "
            f"path.regexpMatch({pattern})"
            ")"
        )
    return _QUERY.replace("__CALLER_SCOPE__", scope)


def _ql_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _anchor_predicate(
    name: str, methods: Sequence[IntegrationMethod], *, input_anchor: bool
) -> str:
    selected = [
        method for method in methods
        if bool(method.input_endpoint_ids if input_anchor else method.output_endpoint_ids)
    ]
    clauses = [
        "(method.getFile().getRelativePath() = "
        f"{_ql_string(method.path)} and method.getLocation().getStartLine() = {method.start_line})"
        for method in selected
    ]
    body = "\n    or\n    ".join(clauses) or "false"
    return f"""predicate {name}(Method method) {{
  method.fromSource() and (
    {body}
  )
}}"""


def _reachability_query(methods: Sequence[IntegrationMethod]) -> str:
    """Build a reverse OUT-to-IN reachability query scoped to indexed methods."""
    return f"""import java
import semmle.code.java.dispatch.VirtualDispatch

class SourceMethodCall extends MethodCall {{
  SourceMethodCall() {{ this.getEnclosingCallable().fromSource() }}
}}

// Keep the exact and possible relations separate.  The final query can then
// report lower confidence when a route exists only through possible dispatch.
predicate exactTarget(MethodCall call, Method target) {{
  target = exactVirtualMethod(call) and target.fromSource()
}}

predicate possibleTarget(MethodCall call, Method target) {{
  target = exactVirtualMethod(call) and target.fromSource()
  or
  not exists(Method exact | exactTarget(call, exact)) and
  target = viableCallable(call) and target.fromSource()
  or
  not exists(Method exact | exactTarget(call, exact)) and
  target = call.getMethod() and target.fromSource()
}}

predicate exactEdge(Callable caller, Callable callee) {{
  exists(SourceMethodCall call |
    call.getEnclosingCallable() = caller and exactTarget(call, callee)
  )
}}

predicate possibleEdge(Callable caller, Callable callee) {{
  exists(SourceMethodCall call |
    call.getEnclosingCallable() = caller and possibleTarget(call, callee)
  )
}}

// The recursion is written backwards: it starts at an indexed output method
// and walks through callers until it reaches an indexed input method.  CodeQL
// computes the transitive closure of these recursive predicates.
predicate exactCallerReachable(Callable callee, Callable caller) {{
  outputAnchor(callee) and exactEdge(caller, callee)
  or
  exists(Callable previous |
    exactCallerReachable(callee, previous) and exactEdge(caller, previous)
  )
}}

predicate possibleCallerReachable(Callable callee, Callable caller) {{
  outputAnchor(callee) and possibleEdge(caller, callee)
  or
  exists(Callable previous |
    possibleCallerReachable(callee, previous) and possibleEdge(caller, previous)
  )
}}

// These anchors are generated from SystemLens' integration-method inventory.
// They prevent this query from returning arbitrary method-to-method paths.
{_anchor_predicate("inputAnchor", methods, input_anchor=True)}
{_anchor_predicate("outputAnchor", methods, input_anchor=False)}

// Prefer an exact route.  A possible route is reported only when no exact
// route exists for the same input/output pair.
from Method inputMethod, Method outputMethod, string confidence
where outputAnchor(outputMethod) and inputAnchor(inputMethod) and
  inputMethod != outputMethod and
  (exactCallerReachable(outputMethod, inputMethod) and confidence = "medium" or
   not exactCallerReachable(outputMethod, inputMethod) and
   possibleCallerReachable(outputMethod, inputMethod) and confidence = "low")
select inputMethod.getQualifiedName() as source,
  inputMethod.getFile().getRelativePath() as source_path,
  inputMethod.getLocation().getStartLine() as source_line,
  outputMethod.getQualifiedName() as target,
  outputMethod.getFile().getRelativePath() as target_path,
  outputMethod.getLocation().getStartLine() as target_line, confidence
"""


def codeql_executable() -> str | None:
    """Return the local CodeQL executable, when it is available."""
    return shutil.which("codeql")


def _run_with_progress(
    command: list[str],
    *,
    timeout: float,
    progress: Callable[[str], None] | None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run CodeQL with one timeout and process-group cleanup policy."""
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if progress is not None else subprocess.PIPE,
        text=True, bufsize=1, cwd=cwd, start_new_session=os.name == "posix",
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


def _prepare_source_only_root(repo_root: Path, destination: Path) -> int:
    """Copy application and generated Java sources without build descriptors.

    Maven projects may keep AsyncAPI/OpenAPI Java sources below
    ``target/generated-sources``. They are needed to resolve calls whose
    signatures use generated DTOs, while the rest of ``target`` remains an
    untrusted build artifact and is intentionally excluded.
    """
    excluded_directories = {".git", ".systemlens", "target", "out"}
    copied = 0
    for source in repo_root.rglob("*.java"):
        if not source.is_file():
            continue
        relative = source.relative_to(repo_root)
        parts = relative.parts
        generated_target = any(
            parts[index:index + 2] == ("target", "generated-sources")
            for index in range(len(parts) - 1)
        )
        if (
            any(part in excluded_directories for part in parts)
            or is_build_output(relative.as_posix())
        ) and not generated_target:
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


def _prepare_generation_workspace(repo_root: Path, destination: Path) -> None:
    """Copy the project to a disposable workspace before source generation."""
    shutil.copytree(
        repo_root,
        destination,
        ignore=shutil.ignore_patterns(
            ".git", ".systemlens", ".venv", "node_modules", "target", "build", "out"
        ),
    )


def _generate_sources(
    workspace: Path, *, timeout: float, progress: Callable[[str], None] | None,
) -> None:
    """Run build-tool source generation only, never compilation or tests."""
    if (workspace / "pom.xml").is_file():
        command = ["mvn", "-B", "-ntp", "-o", "generate-sources"]
    elif (workspace / "gradlew").is_file():
        command = ["./gradlew", "--no-daemon", "generateSources"]
    else:
        return
    completed = _run_with_progress(command, timeout=timeout, progress=progress, cwd=workspace)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise CodeQLError(f"Source generation failed: {detail}")


@contextmanager
def automatic_codeql_database(
    repo_root: Path, timeout_seconds: int = 600, threads: int = 1,
    ram_mb: int | None = None, verbosity: str | None = None,
    progress: Callable[[str], None] | None = None,
    generate_sources: bool = False,
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
        _prepare_source_only_root(codeql_input, source_root)
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


def extract_codeql_calls(
    database: Path,
    executable: str | None = None,
    timeout_seconds: int = 600,
    path_prefix: str = "",
    caller_prefix: str = "",
    threads: int = 1,
    ram_mb: int | None = None,
    verbosity: str | None = None,
    progress: Callable[[str], None] | None = None,
    deadline: float | None = None,
) -> list[CodeQLCall]:
    """Return statically resolved Java method calls from one CodeQL database.

    A database created for a build module reports paths relative to that
    module. ``path_prefix`` maps those paths back to the indexed repository
    root so that calls from several module databases can be joined together.
    ``caller_prefix`` restricts a global database query to callers below one
    repository-relative project prefix, while retaining callees from other
    projects for cross-project joins.
    """
    if not database.is_dir():
        raise CodeQLError(f"CodeQL database not found: {database}")
    executable = executable or codeql_executable()
    if executable is None:
        raise CodeQLError("CodeQL executable not found.")
    with tempfile.TemporaryDirectory(prefix="systemlens-codeql-") as directory:
        work = Path(directory)
        query = work / "calls.ql"
        query.write_text(_calls_query(caller_prefix), encoding="utf-8")
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
        try:
            completed = _run_with_progress(
                command, timeout=_remaining_timeout(timeout_seconds, deadline), progress=progress,
            )
        except subprocess.TimeoutExpired as exc:
            partial_calls: list[CodeQLCall] = []
            if bqrs.is_file():
                try:
                    decoded_partial = _decode_bqrs(
                        executable, bqrs, output,
                        timeout=max(0.01, min(timeout_seconds, 5, _remaining_timeout(timeout_seconds, deadline))),
                    )
                    if decoded_partial.returncode == 0 and output.is_file():
                        with output.open(newline="", encoding="utf-8") as handle:
                            partial_calls = _parse_codeql_calls(csv.DictReader(handle), path_prefix)
                except (OSError, subprocess.TimeoutExpired, CodeQLError):
                    pass
            raise CodeQLTimeout(command, timeout_seconds, calls=partial_calls) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL call graph failed: {detail}")
        decoded = _decode_bqrs(
            executable, bqrs, output,
            timeout=_remaining_timeout(timeout_seconds, deadline),
        )
        if decoded.returncode != 0:
            detail = (decoded.stderr or decoded.stdout).strip()
            raise CodeQLError(f"CodeQL call graph decoding failed: {detail}")
        try:
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise CodeQLError("CodeQL did not produce a CSV call graph.") from exc
    return _parse_codeql_calls(rows, path_prefix)


def _parse_codeql_calls(rows: Iterable[Mapping[str, str]], path_prefix: str) -> list[CodeQLCall]:
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


def extract_codeql_kafka_message_types(
    database: Path,
    *,
    executable: str | None = None,
    timeout_seconds: int = 600,
    threads: int = 1,
    ram_mb: int | None = None,
    deadline: float | None = None,
) -> list[CodeQLKafkaMessageType]:
    """Return unique payload types for Strategy1 Kafka producer calls."""
    if not database.is_dir():
        raise CodeQLError(f"CodeQL database not found: {database}")
    executable = executable or codeql_executable()
    if executable is None:
        raise CodeQLError("CodeQL executable not found.")
    with tempfile.TemporaryDirectory(prefix="systemlens-codeql-kafka-") as directory:
        work = Path(directory)
        query = work / "kafka_message_types.ql"
        query.write_text(_KAFKA_MESSAGE_TYPES_QUERY, encoding="utf-8")
        (work / "qlpack.yml").write_text(_QLPACK, encoding="utf-8")
        bqrs = work / "kafka_message_types.bqrs"
        output = work / "kafka_message_types.csv"
        command = [
            executable, "query", "run", str(query), f"--database={database}",
            f"--output={bqrs}", f"--threads={threads}",
        ]
        if ram_mb is not None:
            command.append(f"--ram={ram_mb}")
        user_packs = Path.home() / ".codeql" / "packages"
        if user_packs.is_dir():
            command.append(f"--additional-packs={user_packs}")
        completed = _run_with_progress(
            command, timeout=_remaining_timeout(timeout_seconds, deadline), progress=None,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL Kafka message-type query failed: {detail}")
        decoded = _decode_bqrs(
            executable, bqrs, output,
            timeout=_remaining_timeout(timeout_seconds, deadline),
        )
        if decoded.returncode != 0:
            detail = (decoded.stderr or decoded.stdout).strip()
            raise CodeQLError(f"CodeQL Kafka message-type decoding failed: {detail}")
        try:
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise CodeQLError("CodeQL did not produce a Kafka message-type CSV.") from exc
    result: list[CodeQLKafkaMessageType] = []
    for row in rows:
        try:
            result.append(CodeQLKafkaMessageType(
                path=row["path"], line=int(row["line"]), message_type=row["message_type"],
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CodeQLError("CodeQL returned an unexpected Kafka message-type CSV schema.") from exc
    return result


def extract_codeql_reachability(
    database: Path,
    methods: Sequence[IntegrationMethod],
    *,
    executable: str | None = None,
    timeout_seconds: int = 600,
    threads: int = 1,
    ram_mb: int | None = None,
    deadline: float | None = None,
) -> list[CodeQLReachability]:
    """Return unbounded CodeQL reachability between indexed input/output methods."""
    if not database.is_dir():
        raise CodeQLError(f"CodeQL database not found: {database}")
    if not any(method.input_endpoint_ids for method in methods):
        return []
    if not any(method.output_endpoint_ids for method in methods):
        return []
    executable = executable or codeql_executable()
    if executable is None:
        raise CodeQLError("CodeQL executable not found.")
    with tempfile.TemporaryDirectory(prefix="systemlens-codeql-reachability-") as directory:
        work = Path(directory)
        query = work / "reachability.ql"
        query.write_text(_reachability_query(methods), encoding="utf-8")
        (work / "qlpack.yml").write_text(_QLPACK, encoding="utf-8")
        bqrs = work / "reachability.bqrs"
        output = work / "reachability.csv"
        command = [
            executable, "query", "run", str(query), f"--database={database}",
            f"--output={bqrs}", f"--threads={threads}",
        ]
        if ram_mb is not None:
            command.append(f"--ram={ram_mb}")
        user_packs = Path.home() / ".codeql" / "packages"
        if user_packs.is_dir():
            command.append(f"--additional-packs={user_packs}")
        completed = _run_with_progress(
            command, timeout=_remaining_timeout(timeout_seconds, deadline), progress=None,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CodeQLError(f"CodeQL reachability query failed: {detail}")
        decoded = _decode_bqrs(
            executable, bqrs, output,
            timeout=_remaining_timeout(timeout_seconds, deadline),
        )
        if decoded.returncode != 0:
            detail = (decoded.stderr or decoded.stdout).strip()
            raise CodeQLError(f"CodeQL reachability decoding failed: {detail}")
        try:
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise CodeQLError("CodeQL did not produce a reachability CSV.") from exc
    reachability: list[CodeQLReachability] = []
    for row in rows:
        try:
            reachability.append(CodeQLReachability(
                source=row["source"], source_path=row["source_path"],
                source_line=int(row["source_line"]), target=row["target"],
                target_path=row["target_path"], target_line=int(row["target_line"]),
                confidence=row["confidence"],
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CodeQLError("CodeQL returned an unexpected reachability schema.") from exc
    return reachability
