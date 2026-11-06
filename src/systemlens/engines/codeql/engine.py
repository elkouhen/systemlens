"""CodeQL as a ``CallGraphEngine``: typed virtual dispatch, needs the CLI."""

import hashlib
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from systemlens.callgraph.engine import EngineRequest, EngineSettings
from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.domain.code_flows import IntegrationMethod
import systemlens.engines.codeql.database as database
import systemlens.engines.codeql.extraction as extraction
from systemlens.engines.codeql.declaration import DECLARATION
from systemlens.engines.codeql.errors import CodeQLError, CodeQLTimeout
from systemlens.indexing.source_projection import executable_identity


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
            "verbosity": self._engine.verbosity or ("progress++" if verbose else None),
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

    name = DECLARATION.name
    label = DECLARATION.label
    supports_module_refresh = DECLARATION.supports_module_refresh
    resolves_types = DECLARATION.resolves_types

    def __init__(self, *, database: Path | None = None, threads: int = 1,
                 ram_mb: int | None = None, verbosity: str | None = None) -> None:
        self.database = database
        self.threads = threads
        self.ram_mb = ram_mb
        self.verbosity = verbosity

    def available(self) -> bool:
        return self.database is not None or database.codeql_executable() is not None

    def input_fingerprint(self) -> str:
        """Identify a supplied database, or the CLI that builds the automatic one.

        The automatic database is keyed on the CodeQL executable (ADR-84):
        a new CodeQL recomputes the flows of unchanged sources. The value
        is a hash, so no local path enters the flow signature.
        """
        if self.database is None:
            executable = database.codeql_executable()
            if executable is None:
                return ""
            identity = executable_identity(executable)
            return "codeql-cli-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
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
        if self.verbosity is not None:
            options.update(verbosity=self.verbosity)
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


def create_engine(settings: EngineSettings) -> CodeQLEngine:
    """Registry entry point used by ``systemlens.callgraph.registry``."""
    database = settings.options.get("database")
    verbosity = settings.options.get("verbosity")
    return CodeQLEngine(
        database=Path(str(database)) if database is not None else None,
        threads=settings.threads,
        ram_mb=settings.ram_mb,
        verbosity=str(verbosity) if verbosity is not None else None,
    )
