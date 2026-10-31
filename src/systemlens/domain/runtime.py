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
