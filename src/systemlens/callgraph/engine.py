"""The seam between SystemLens' flow construction and a call-graph engine.

The indexing core asks an engine for source-located calls and, optionally,
for direct reachability answers and Kafka payload types. It never imports an
engine implementation: engines are loaded by name through the registry.

Each engine package publishes an ``EngineDeclaration`` (ADR-85): its name,
label, capabilities, availability check, engine-owned options and ``init``
profile. The configuration, the CLI and the indexing stage read only these
declarations, so none of them names a particular engine.
"""

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.domain.code_flows import IntegrationMethod


OptionKind = Literal["str", "path", "int"]


@dataclass(frozen=True)
class EngineOption:
    """One engine-owned setting, read from ``analysis.engines.<name>.<option>``.

    ``choices`` restricts a string value. A ``path`` value stays relative
    to the indexed root until the engine is built. ``None`` always means
    "unset" and is accepted for every kind.
    """

    name: str
    description: str
    kind: OptionKind = "str"
    default: object = None
    choices: tuple[str, ...] = ()

    def parse(self, value: object) -> object:
        """Return the validated value; raise ``ValueError`` with the reason."""
        if value is None:
            return None
        if self.kind == "int":
            if isinstance(value, bool):
                raise ValueError("doit être un entier positif")
            try:
                number = int(str(value))
            except ValueError as exc:
                raise ValueError("doit être un entier positif") from exc
            if number < 1:
                raise ValueError("doit être un entier positif")
            return number
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError("doit être une chaîne non vide")
        text = str(value).strip()
        if self.choices and text not in self.choices:
            raise ValueError(f"valeurs autorisées : {', '.join(self.choices)}")
        return text


@dataclass(frozen=True)
class EngineSettings:
    """What every engine receives when it is built.

    ``threads`` and ``ram_mb`` are the generic ``analysis.engine_*`` bounds;
    zero threads means one per available core. ``options`` holds the
    validated engine-owned options, keyed by option name; a ``path`` option
    is already resolved against ``repo_root`` when one is known.
    """

    threads: int = 0
    ram_mb: int | None = None
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineDeclaration:
    """What an engine package publishes to the registry (ADR-85).

    ``factory`` is ``"module:function"``; the registry imports it only when
    the engine is built, so declaring an engine never loads its analyzer.
    ``unavailable_reason`` returns ``None`` when the engine can run, else a
    sentence telling the user what is missing. ``database_option`` names
    the option that points at a prebuilt analyzer database, when the
    engine accepts one. ``artifact_directory`` tells the file inventory
    that a directory is an engine artifact, never a source tree.
    """

    name: str
    label: str
    description: str
    factory: str
    unavailable_reason: Callable[[], str | None]
    supports_module_refresh: bool = False
    resolves_types: bool = False
    options: tuple[EngineOption, ...] = ()
    database_option: str | None = None
    init_profile: bool = False
    default_excludes: tuple[str, ...] = ()
    artifact_directory: Callable[[Path], bool] | None = None

    def option(self, name: str) -> EngineOption:
        for option in self.options:
            if option.name == name:
                return option
        known = ", ".join(option.name for option in self.options) or "aucune"
        raise ValueError(
            f"Option inconnue pour le moteur {self.name} : {name!r}. Options : {known}."
        )

    def defaults(self) -> dict[str, object]:
        return {option.name: option.default for option in self.options}


@dataclass(frozen=True)
class EngineRequest:
    """What one index run asks of an engine."""

    repo_root: Path
    allowed_paths: Sequence[str]
    timeout_seconds: int
    deadline: float | None = None
    # Generated build sources are already present in the repository.
    include_generated_sources: bool = False
    progress: Callable[[str], None] | None = None
    verbose: bool = False


class CallGraphSession(Protocol):
    """One prepared analysis of a repository, valid inside ``open``."""

    def calls(self) -> list[MethodCall]: ...

    def methods(self, source_prefix: str = "") -> list[SourceMethod]: ...

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]: ...

    def payload_types(self) -> list[PayloadTypeEvidence]: ...


class CallGraphEngine(Protocol):
    """A pluggable producer of source-located Java method calls.

    ``supports_module_refresh`` engines can recompute one module against a
    persisted global graph. ``resolves_types`` engines answer from a typed
    program model; others leave resolution to the shared source symbol pass.
    """

    name: str
    label: str
    supports_module_refresh: bool
    resolves_types: bool

    def available(self) -> bool: ...

    def input_fingerprint(self) -> str:
        """Identify the analysis input the engine reads besides the sources.

        Empty when the indexed sources are the engine's whole input. A
        different value makes the next index recompute the flows.
        """
        ...

    def open(self, request: EngineRequest) -> AbstractContextManager[CallGraphSession]: ...
