"""CodeQL as a ``CallGraphEngine``: typed virtual dispatch, needs the CLI."""

import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from systemlens.callgraph.engine import EngineRequest
from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.domain.code_flows import IntegrationMethod
import systemlens.engines.codeql.database as database
import systemlens.engines.codeql.extraction as extraction
from systemlens.engines.codeql.errors import CodeQLError, CodeQLTimeout


def _deadline_bound(
    function: Callable[..., object], *args: object, **kwargs: object,
) -> object:
    """Report any CodeQL or process deadline as a call-graph timeout."""
    try:
        return function(*args, **kwargs)
    except CodeQLTimeout:
        raise
    except subprocess.TimeoutExpired as exc:
        raise CodeQLTimeout(list(exc.cmd or ["codeql"]), exc.timeout) from exc


class _CodeQLSession:
    """Queries against one CodeQL database, valid inside ``CodeQLEngine.open``."""

    def __init__(self, engine: "CodeQLEngine", database: Path, request: EngineRequest) -> None:
        self._engine = engine
        self._database = database
        self._request = request

    def _common(self) -> dict[str, object]:
        return {
            "timeout_seconds": self._request.timeout_seconds,
            "threads": self._engine.threads,
            "ram_mb": self._engine.ram_mb,
            "deadline": self._request.deadline,
        }

    def _logging(self) -> dict[str, object]:
        verbose = self._request.verbose
        return {
            "verbosity": "progress++" if verbose else None,
            "progress": self._request.progress if verbose else None,
        }

    def calls(self) -> list[MethodCall]:
        calls = cast(list[MethodCall], _deadline_bound(
            extraction.extract_codeql_calls, self._database, **self._common(), **self._logging(),
        ))
        indexed = {path for path in self._request.allowed_paths if path.endswith(".java")}
        if calls and indexed and not any(call.caller_path in indexed for call in calls):
            # No row names an indexed file: the join would keep only the
            # symbol pass's edges under CodeQL's name.
            raise CodeQLError(
                f"None of the {len(calls)} CodeQL call(s) comes from an indexed Java file: "
                "the database was built from another source root."
            )
        return calls

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        return cast(list[SourceMethod], _deadline_bound(
            extraction.extract_codeql_methods, self._database, **self._common(), **self._logging(),
            source_prefix=source_prefix,
        ))

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        return cast(list[MethodReachability], _deadline_bound(
            extraction.extract_codeql_reachability, self._database, methods, **self._common(),
        ))

    def payload_types(self) -> list[PayloadTypeEvidence]:
        return cast(list[PayloadTypeEvidence], _deadline_bound(
            extraction.extract_codeql_kafka_message_types, self._database, **self._common(),
        ))


class CodeQLEngine:
    """CodeQL as a call-graph engine: typed virtual dispatch, needs the CLI."""

    name = "codeql"
    label = "CodeQL"
    supports_module_refresh = True
    resolves_types = True

    def __init__(self, *, database: Path | None = None, threads: int = 1,
                 ram_mb: int | None = None) -> None:
        self.database = database
        self.threads = threads
        self.ram_mb = ram_mb

    def available(self) -> bool:
        return self.database is not None or database.codeql_executable() is not None

    def input_fingerprint(self) -> str:
        """Identify a supplied database; the automatic one derives from the sources."""
        if self.database is None:
            return ""
        return database.database_fingerprint(self.database)

    @contextmanager
    def open(self, request: EngineRequest) -> Iterator[_CodeQLSession]:
        if self.database is not None:
            # A supplied database must represent the indexed sources: one
            # built from another revision or source root gives false edges
            # or none at all (call-graph review Q1, Q5).
            database.verify_database_sources(
                self.database, request.repo_root, request.allowed_paths,
            )
            yield _CodeQLSession(self, self.database, request)
            return
        options: dict[str, Any] = {
            "timeout_seconds": request.timeout_seconds,
            "threads": self.threads,
            "ram_mb": self.ram_mb,
            "allowed_paths": list(request.allowed_paths),
            "deadline": request.deadline,
        }
        if request.verbose:
            options.update(verbosity="progress++", progress=request.progress)
        if request.include_generated_sources:
            # Source generation already ran before the inventory; reuse its
            # files instead of running the build tool twice.
            options.update(generate_sources=False, include_target_java_sources=True)
        try:
            with database.automatic_codeql_database(request.repo_root, **options) as opened:
                if opened is None:
                    raise CodeQLError("CodeQL executable not found.")
                yield _CodeQLSession(self, opened, request)
        except CodeQLTimeout:
            raise
        except subprocess.TimeoutExpired as exc:
            raise CodeQLTimeout(list(exc.cmd or ["codeql"]), exc.timeout) from exc


def create_engine(*, database: Path | None = None, threads: int = 1,
                  ram_mb: int | None = None) -> CodeQLEngine:
    """Registry entry point used by ``systemlens.callgraph.registry``."""
    return CodeQLEngine(database=database, threads=threads, ram_mb=ram_mb)
