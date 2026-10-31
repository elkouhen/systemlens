"""Runtime facts attached conservatively to indexed architecture modules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class KubernetesWorkload:
    """Resource dimensions aggregated across regular workload containers."""

    kind: str
    namespace: str
    name: str
    replicas: int | None
    cpu_request_millicores: int | None
    memory_request_bytes: int | None
    cpu_limit_millicores: int | None
    memory_limit_bytes: int | None


@dataclass(frozen=True)
class RuntimeObservation:
    """Aggregated runtime traffic for one service and target over a window.

    ``kind`` is ``destination`` for outgoing calls or ``transaction`` for
    handled requests and messages. Counts are lower bounds: they depend on
    the telemetry agent's sampling.
    """

    id: str
    source: str
    origin: str
    kind: str
    service_name: str
    target: str
    target_type: str | None
    calls: int
    failures: int
    duration_sum_us: float | None
    window_start: str
    window_end: str
    collected_at: str


@dataclass(frozen=True)
class RuntimeErrorGroup:
    """Errors one APM service reported for one grouping key over a window.

    Only the exception type, the code location (``culprit``) and the
    transaction name are kept. Error messages are never persisted: they can
    carry business data or secrets. ``occurrences`` is a sampled lower bound.
    """

    id: str
    source: str
    origin: str
    service_name: str
    grouping_key: str
    exception_type: str | None
    culprit: str | None
    transaction_name: str | None
    occurrences: int
    last_seen: str | None
    window_start: str
    window_end: str
    collected_at: str
