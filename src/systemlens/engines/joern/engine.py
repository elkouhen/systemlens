"""Joern as a ``CallGraphEngine``: a code property graph built without a build.

``open`` projects the indexed Java sources (the same projection as every
external engine), builds a CPG with ``joern-parse --language javasrc``,
runs ``export.sc`` once, and answers ``calls`` and ``methods`` from the
exported JSON. The CPG is cached in ``.systemlens/joern/`` with a
fingerprint of the projection and of the Joern installation, as ADR-84
does for CodeQL. Joern reports no reachability, no Kafka payload type and
no module refresh; the shared source passes keep those duties (ADR-85).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from functools import cache
from pathlib import Path
from typing import Any

from systemlens.callgraph.engine import EngineRequest, EngineSettings
from systemlens.callgraph.model import (
    CallGraphTimeout,
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.engines.joern.declaration import DECLARATION, unavailable_reason
from systemlens.engines.joern.extraction import JoernError, parse_calls, parse_methods
from systemlens.indexing.source_generation import remaining_timeout, run_with_progress
from systemlens.indexing.source_projection import (
    executable_identity,
    project_java_sources,
    projection_fingerprint,
)

CACHE_DIRECTORY = Path(".systemlens") / "joern"
EXPORT_SCRIPT = Path(__file__).with_name("export.sc")
_RELEASE_IN_PATH = re.compile(r"/joern/(\d+\.\d+\.\d+)/")


@cache
def joern_version() -> str:
    """The installed Joern, named by its CLI jar or, failing that, its launcher.

    Reading the jar name avoids starting a JVM. A Homebrew or release
    distribution keeps ``io.joern.joern-cli-<version>.jar`` in ``lib/``
    next to the resolved launcher.
    """
    launcher = shutil.which("joern")
    if launcher is None:
        return ""
    candidates = [Path(launcher).resolve().parent]
    try:
        # Homebrew's ``bin/joern`` is a shell wrapper around ``libexec/joern``.
        text = Path(launcher).read_text(encoding="utf-8", errors="ignore")
        candidates.extend(
            Path(token.strip('"')).parent for token in text.split()
            if token.strip('"').endswith("/joern") and Path(token.strip('"')).exists()
        )
    except OSError:
        pass
    for directory in candidates:
        jars = sorted((directory / "lib").glob("io.joern.joern-cli-*.jar"))
        if jars:
            version = jars[-1].name.removeprefix("io.joern.joern-cli-").removesuffix(".jar")
            # A source build names its jar ``HEAD+<date>``; Homebrew keeps the
            # release number in the installation path.
            release = _RELEASE_IN_PATH.search(directory.as_posix())
            return f"{release.group(1)} ({version})" if release else version
    return executable_identity(launcher)


def _jvm_environment(threads: int, ram_mb: int | None) -> dict[str, str]:
    """``JAVA_OPTS`` for both launchers: heap limit and processor count."""
    options = [os.environ.get("JAVA_OPTS", "")]
    if ram_mb is not None:
        options.append(f"-Xmx{ram_mb}m")
    if threads > 0:
        options.append(f"-XX:ActiveProcessorCount={threads}")
    value = " ".join(option for option in options if option)
    return {"JAVA_OPTS": value} if value else {}


def _timeout(exc: subprocess.TimeoutExpired) -> CallGraphTimeout:
    return CallGraphTimeout(f"Joern deadline exceeded after {exc.timeout:.0f} s: {exc.cmd}")


class _JoernSession:
    """Answers from one exported CPG, valid inside ``JoernEngine.open``."""

    def __init__(self, document: dict[str, Any], indexed_paths: Sequence[str]) -> None:
        self._document = document
        self._indexed = [path for path in indexed_paths if path.endswith(".java")]

    def calls(self) -> list[MethodCall]:
        return parse_calls(self._document, self._indexed)

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        return parse_methods(self._document, self._indexed, source_prefix)

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        return []

    def payload_types(self) -> list[PayloadTypeEvidence]:
        return []


class JoernEngine:
    """Joern as a call-graph engine: typed Java CPG from sources, needs a JDK."""

    name = DECLARATION.name
    label = DECLARATION.label
    supports_module_refresh = DECLARATION.supports_module_refresh
    resolves_types = DECLARATION.resolves_types

    def __init__(self, *, threads: int = 0, ram_mb: int | None = None) -> None:
        self.threads = threads
        self.ram_mb = ram_mb

    def available(self) -> bool:
        return unavailable_reason() is None

    def input_fingerprint(self) -> str:
        """The Joern version: a new Joern recomputes the flows."""
        version = joern_version()
        return f"joern-{version}" if version else ""

    @contextmanager
    def open(self, request: EngineRequest) -> Iterator[_JoernSession]:
        reason = unavailable_reason()
        if reason is not None:
            raise JoernError(reason)
        progress = request.progress if request.verbose else None
        environment = _jvm_environment(self.threads, self.ram_mb)
        try:
            with tempfile.TemporaryDirectory(prefix="systemlens-joern-") as directory:
                workspace = Path(directory)
                source_root = workspace / "source"
                source_root.mkdir()
                project_java_sources(
                    request.repo_root, source_root,
                    include_target_java_sources=request.include_generated_sources,
                    allowed_paths=request.allowed_paths,
                )
                cpg = self._cpg(request, source_root, workspace, environment, progress)
                document = self._export(request, cpg, workspace, environment, progress)
                yield _JoernSession(document, request.allowed_paths)
        except subprocess.TimeoutExpired as exc:
            raise _timeout(exc) from exc

    def _cpg(
        self, request: EngineRequest, source_root: Path, workspace: Path,
        environment: dict[str, str], progress: Callable[[str], None] | None,
    ) -> Path:
        """Reuse the cached CPG of the same projection, or build and cache one."""
        cache = request.repo_root / CACHE_DIRECTORY
        cached = cache / "cpg.bin"
        stamp = cache / "cpg.fingerprint"
        fingerprint = projection_fingerprint(
            source_root, f"joern-parse javasrc\n{joern_version()}\n",
        )
        try:
            if stamp.read_text(encoding="utf-8").strip() == fingerprint and cached.is_file():
                if progress is not None:
                    progress("Joern : CPG réutilisé, sources inchangées.")
                return cached
        except OSError:
            pass
        built = workspace / "cpg.bin"
        completed = run_with_progress(
            ["joern-parse", "--language", "javasrc", "-o", str(built), str(source_root)],
            timeout=remaining_timeout(request.timeout_seconds, request.deadline),
            progress=progress, cwd=workspace, env=environment,
        )
        if completed.returncode != 0 or not built.is_file():
            detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
            raise JoernError(f"joern-parse failed: {detail}")
        try:
            cache.mkdir(parents=True, exist_ok=True)
            stamp.unlink(missing_ok=True)
            shutil.copyfile(built, cached)
            stamp.write_text(fingerprint + "\n", encoding="utf-8")
        except OSError:
            return built
        return cached

    def _export(
        self, request: EngineRequest, cpg: Path, workspace: Path,
        environment: dict[str, str], progress: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        """Run ``export.sc`` on a copy of the CPG and read its JSON."""
        output = workspace / "export.json"
        # Joern writes a project workspace in its working directory.
        script_directory = workspace / "script"
        script_directory.mkdir()
        completed = run_with_progress(
            [
                "joern", "--script", str(EXPORT_SCRIPT),
                "--param", f"cpgFile={cpg}", "--param", f"outFile={output}",
            ],
            timeout=remaining_timeout(request.timeout_seconds, request.deadline),
            progress=progress, cwd=script_directory, env=environment,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
            raise JoernError(f"Joern export script failed: {detail}")
        try:
            document = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise JoernError("Joern export is not valid JSON.") from exc
        if not isinstance(document, dict):
            raise JoernError("Joern export is not a JSON object.")
        return document


def create_engine(settings: EngineSettings) -> JoernEngine:
    """Registry entry point used by ``systemlens.callgraph.registry``."""
    return JoernEngine(threads=settings.threads, ram_mb=settings.ram_mb)
