"""Engine-neutral facts exchanged between call-graph engines and the core.

An engine reports source-located method calls; the core joins them to
indexed integration methods. No type here depends on a particular analyzer.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MethodCall:
    """One call site from a source method to a source method.

    ``dispatch_confidence`` is ``exact`` when the engine identified a single
    target, ``possible`` when the target is one of several candidates.
    ``caller_parameter_types`` and ``callee_parameter_types`` give each
    declaration's parameter types when the engine knows them, so the join
    tells apart same-name overloads declared on one line. They do not take
    part in a call's identity.
    """

    caller: str
    caller_path: str
    caller_line: int
    callee: str
    callee_path: str
    callee_line: int
    call_line: int
    dispatch_confidence: str = "exact"
    caller_parameter_types: tuple[str, ...] | None = field(default=None, compare=False)
    callee_parameter_types: tuple[str, ...] | None = field(default=None, compare=False)


@dataclass(frozen=True)
class SourceMethod:
    """One source-backed Java method reported directly by an engine."""

    qualified_method: str
    path: str
    start_line: int


@dataclass(frozen=True)
class MethodReachability:
    """A direct engine answer for one indexed input/output method pair."""

    source: str
    source_path: str
    source_line: int
    target: str
    target_path: str
    target_line: int
    confidence: str


@dataclass(frozen=True)
class PayloadTypeEvidence:
    """A source-backed payload type found at a Kafka send site."""

    path: str
    line: int
    message_type: str


class CallGraphError(RuntimeError):
    """An engine is installed but failed; coverage must not shrink silently."""


class CallGraphTimeout(RuntimeError):
    """An engine deadline, with the calls an engine completed before it.

    The index keeps these calls, and the calls of any query that completed
    before the deadline, in its partial snapshot.
    """

    def __init__(self, message: str = "call-graph deadline exceeded", *,
                 calls: list[MethodCall] | None = None) -> None:
        super().__init__(message)
        self.calls = calls or []
